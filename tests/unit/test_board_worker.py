"""Tests for headless board worker handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot_coms import Client, Worker
from bot_coms.spool import init_spool

from bot_coms_board.coordinator import TeamCoordinator
from bot_coms_board.handler import make_team_handler
from bot_coms_board.payload import SlicePayload, sha256_assignment_spec


def _digest(path: Path) -> str:
    return sha256_assignment_spec(path)


def _audit_events(spool: Path, peer: str, msg_id: str) -> list[dict]:
    audit_path = spool / peer / "state" / "audit.jsonl"
    if not audit_path.is_file():
        return []
    events = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("msg_id") == msg_id:
            events.append(ev)
    return events


@pytest.fixture
def worker_env(tmp_path: Path, monkeypatch):
    team_root = tmp_path / ".hermes" / "team"
    spool = team_root / "spool"
    init_spool(spool, ["pm", "swe"])
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(spool))
    return team_root, spool


class TestBoardWorker:
    def test_report_only_single_ack_no_nack(self, worker_env):
        team_root, spool = worker_env
        ctx = team_root / "context" / "SMOKE.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("# smoke\n", encoding="utf-8")
        digest = _digest(ctx)

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.register_slice(
            slice_id="SMOKE",
            title="smoke",
            assignment_path=str(ctx),
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
        )

        payload = SlicePayload(schema_version="1.0", intent="report_only", slice="SMOKE")
        pm = Client(spool, "pm")
        env = pm.send(
            "swe",
            "request",
            payload.to_dict(),
            correlation_id="SMOKE",
            idempotency_key=payload.idempotency_key(digest),
            reply_to="pm",
        )
        pm.close()
        msg_id = env.id

        client = Client(spool, "swe")
        handler = make_team_handler(peer="swe", team_root=team_root, spool_root=spool)
        worker = Worker(client, handler, poll_interval_s=0.01)
        worker.run_until_idle(idle_rounds=3)
        client.close()

        events = _audit_events(spool, "swe", msg_id)
        assert any(e["event"] == "acked" for e in events)
        assert not any(e["event"] == "nacked" for e in events)
        assert list((spool / "swe" / "acked").glob(f"{msg_id}.json"))

    def test_assign_not_auto_handled_by_process_claim(self, worker_env):
        team_root, spool = worker_env
        ctx = team_root / "context" / "S9.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("work\n", encoding="utf-8")
        digest = _digest(ctx)

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.register_slice(
            slice_id="S9",
            title="work",
            assignment_path=str(ctx),
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
        )

        payload = SlicePayload(schema_version="1.0", intent="assign", slice="S9")
        pm = Client(spool, "pm")
        pm.send(
            "swe",
            "request",
            payload.to_dict(),
            correlation_id="S9",
            idempotency_key=payload.idempotency_key(digest),
            reply_to="pm",
        )
        pm.close()

        client = Client(spool, "swe")
        claimed = client.claim()
        assert claimed is not None
        from bot_coms_board.handler import _WORKER_INTENTS

        decision = coord.process_claim(
            claimed,
            client=client,
            auto_handle=True,
            auto_handle_intents=_WORKER_INTENTS,
        )
        client.close()

        row = coord.store.get_slice("S9")
        assert row is not None
        assert row.status == "QUEUED"
        assert decision.disposition == "launch_cursor"
        assert decision.handled is False
