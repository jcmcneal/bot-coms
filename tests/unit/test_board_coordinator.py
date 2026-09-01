"""Tests for TeamCoordinator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bot_coms import Client
from bot_coms.spool import init_spool

from bot_coms_board.coordinator import TeamCoordinator
from bot_coms_board.payload import SlicePayload


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def coord_env(tmp_path: Path, monkeypatch):
    team_root = tmp_path / ".hermes" / "team"
    spool = team_root / "spool"
    init_spool(spool, ["pm", "swe"])
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(spool))
    return team_root, spool


class TestCoordinator:
    def test_report_only_auto_handled(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "SMOKE.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("# smoke\nstatus: QUEUED\n", encoding="utf-8")
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
        pm.send(
            "swe",
            "request",
            payload.to_dict(),
            correlation_id="SMOKE",
            idempotency_key=payload.idempotency_key(digest),
            reply_to="pm",
        )
        pm.close()

        result = coord.process_inbox("swe", auto_handle=False, auto_handle_intents=frozenset({"report_only"}))
        assert len(result.decisions) == 1
        d = result.decisions[0]
        assert d.disposition == "report_only_complete"
        assert d.handled is True
        assert d.ack_result is not None
        acked = list((spool / "swe" / "acked").glob("*.json"))
        assert len(acked) == 1

    def test_assign_returns_launch_bundle_without_auto_ack(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S9.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("## Assignment\nwork\n", encoding="utf-8")
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
        env = pm.send(
            "swe",
            "request",
            payload.to_dict(),
            correlation_id="S9",
            idempotency_key=payload.idempotency_key(digest),
            reply_to="pm",
        )
        pm.close()
        msg_id = env.id

        result = coord.process_inbox("swe", auto_handle=False, auto_handle_intents=frozenset({"report_only"}))
        assert len(result.decisions) == 1
        d = result.decisions[0]
        assert d.disposition == "launch_cursor"
        assert d.handled is False
        assert "work" in (d.assignment_body or "")
        assert list((spool / "swe" / "acked").glob("*.json")) == []
        assert (spool / "swe" / "processing" / f"{msg_id}.json").is_file()

    def test_assign_async_fire_and_forget(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S10.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("work\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        result = coord.assign(
            slice_id="S10",
            to_peer="swe",
            title="async",
            assignment_path=str(ctx),
        )
        assert result.outbound_id
        assert "ack" not in result.__dict__ or not hasattr(result, "ack_payload")
        inbox = list((spool / "swe" / "inbox").glob("*.json"))
        assert len(inbox) == 1

    def test_assign_idempotent_when_already_running(self, coord_env):
        team_root, spool = coord_env
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
        coord.store.set_status("S9", "RUNNING", active_job="s9-job")

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

        result = coord.process_inbox("swe", auto_handle=False)
        d = result.decisions[0]
        assert d.disposition == "assign_already_running"
        assert d.handled is True
        assert d.ack_result["status"] == "RUNNING"
        assert d.error_code != "SLICE_NOT_FOUND"
