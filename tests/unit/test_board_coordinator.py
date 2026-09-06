"""Tests for TeamCoordinator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot_coms import Client
from bot_coms.atomic import read_json
from bot_coms.spool import init_spool

from bot_coms_board.coordinator import TeamCoordinator
from bot_coms_board.handler import make_team_handler
from bot_coms_board.job_done import sidecar_path
from bot_coms_board.payload import SlicePayload, sha256_assignment_spec


def _digest(path: Path) -> str:
    return sha256_assignment_spec(path)


def _read_inbox_envelope(spool: Path, peer: str) -> dict:
    inbox = spool / peer / "inbox"
    files = sorted(inbox.glob("*.json"))
    assert len(files) == 1
    return read_json(files[0])


@pytest.fixture
def coord_env(tmp_path: Path, monkeypatch):
    team_root = tmp_path / ".hermes" / "team"
    spool = team_root / "spool"
    init_spool(spool, ["pm", "swe"])
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(spool))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")
    return team_root, spool


class TestCoordinator:
    def test_reconcile_recovers_dead_running_when_sidecar_missing(self, coord_env, tmp_path, monkeypatch):
        team_root, spool = coord_env
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        ctx = team_root / "context" / "S20.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("recover me\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.assign(
            slice_id="S20",
            to_peer="swe",
            title="dead-running recover",
            assignment_path=str(ctx),
        )
        coord.store.set_status("S20", "RUNNING", active_job="job-missing-sidecar")

        out = coord.reconcile()

        row = coord.store.get_slice("S20")
        assert row is not None
        assert row.status == "QUEUED"
        assert row.active_job is None
        recovered = [
            item
            for item in out["jobs"]
            if item.get("job") == "job-missing-sidecar" and item.get("reason") == "sidecar_missing"
        ]
        assert len(recovered) == 1
        history = coord.store.workflow_history("S20")
        event = next(e for e in history if e["kind"] == "dead_running_recovered")
        assert event["details"]["job"] == "job-missing-sidecar"
        assert event["details"]["reason"] == "sidecar_missing"

    def test_reconcile_leaves_running_when_sidecar_exists_but_exit_unknown(self, coord_env, tmp_path, monkeypatch):
        team_root, spool = coord_env
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        ctx = team_root / "context" / "S21.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("still running\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.assign(
            slice_id="S21",
            to_peer="swe",
            title="unknown exit",
            assignment_path=str(ctx),
        )
        coord.store.set_status("S21", "RUNNING", active_job="job-unknown-exit")
        sidecar = sidecar_path("job-unknown-exit", home=home)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"slice": "S21", "profile": "software-engineer", "mode": "write"}),
            encoding="utf-8",
        )

        out = coord.reconcile()

        row = coord.store.get_slice("S21")
        assert row is not None
        assert row.status == "RUNNING"
        assert row.active_job == "job-unknown-exit"
        assert all(item.get("job") != "job-unknown-exit" for item in out["jobs"])

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
        assert d.disposition == "launch_agent"
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

    def test_report_after_notes_append_succeeds(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S11.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("## Assignment\nintegrate\n\n## Notes\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.register_slice(
            slice_id="S11",
            title="ancestry report",
            assignment_path=str(ctx),
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
        )
        ctx.write_text(
            "## Assignment\nintegrate\n\n## Notes\n- pytest green\n",
            encoding="utf-8",
        )

        out = coord.report(
            slice_id="S11",
            verdict="LANDED",
            evidence="pytest green",
            from_peer="swe",
        )
        assert out["envelope_id"]

        client = Client(spool, "pm")
        handler = make_team_handler(peer="pm", team_root=team_root, spool_root=spool)
        from bot_coms import Worker

        worker = Worker(client, handler, poll_interval_s=0.01)
        worker.run_until_idle(idle_rounds=3)
        client.close()

        row = coord.store.get_slice("S11")
        assert row is not None
        assert row.verdict == "LANDED"
        report_acked = list((spool / "pm" / "acked").glob("*.json"))
        assert report_acked
        fail_responses = [
            read_json(p)
            for p in (spool / "pm" / "inbox").glob("*.json")
            if read_json(p).get("payload", {}).get("intent") == "fail"
        ]
        assert fail_responses == []

    def test_report_only_reads_expected_assignment_prose_update(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S11b.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("Initial assignment\n", encoding="utf-8")
        digest = _digest(ctx)

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.register_slice(
            slice_id="S11b",
            title="context update",
            assignment_path=str(ctx),
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
        )
        ctx.write_text("Initial assignment\n\nApproved scope update\n", encoding="utf-8")

        payload = SlicePayload(schema_version="1.0", intent="report_only", slice="S11b")
        pm = Client(spool, "pm")
        pm.send(
            "swe",
            "event",
            payload.to_dict(),
            correlation_id="S11b",
            idempotency_key=payload.idempotency_key(digest),
            reply_to="pm",
        )
        pm.close()

        result = coord.process_inbox("swe", limit=1)
        decision = result.decisions[0]
        assert decision.disposition == "report_only_complete"
        assert decision.error_code is None
        assert "Approved scope update" in (decision.assignment_body or "")

    def test_assign_stamps_headers_and_notify_source(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S12.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("work\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        result = coord.assign(
            slice_id="S12",
            to_peer="swe",
            title="headers",
            assignment_path=str(ctx),
            headers={"source": "discord:channel-99"},
        )
        assert result.outbound_id

        env = _read_inbox_envelope(spool, "swe")
        assert env["headers"] == {"delivery":"internal", "source": "discord:channel-99"}
        row = coord.store.get_slice("S12")
        assert row is not None
        assert row.notify_source == "discord:channel-99"

    def test_report_fold_up_preserves_source_for_pm_notify(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S13.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("## Assignment\nx\n\n## Notes\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.assign(
            slice_id="S13",
            to_peer="swe",
            title="notify",
            assignment_path=str(ctx),
            headers={"source": "discord:channel-123"},
        )
        coord.report(slice_id="S13", verdict="LANDED", evidence="tests passed", from_peer="swe")

        result = coord.process_inbox("pm", limit=1)
        assert len(result.decisions) == 1
        assert result.decisions[0].disposition == "report_received"
        ack = result.decisions[0].ack_result
        assert ack is not None
        assert ack["verdict"] == "LANDED"
        assert ack["status"] == "REVIEW"

        result_env = read_json(spool / "pm" / "results" / "S13.json")
        assert result_env["headers"] == {"delivery":"internal", "source": "discord:channel-123"}
        assert result_env["payload"]["intent"] == "ack"
        assert result_env["payload"]["verdict"] == "LANDED"
        assert result_env["payload"]["status"] == "REVIEW"

    def test_landed_clears_running_and_active_job(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S15.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("work\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.register_slice(
            slice_id="S15",
            title="landed",
            assignment_path=str(ctx),
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
        )
        coord.store.set_status("S15", "RUNNING", active_job="s15-job")

        coord.report(
            slice_id="S15",
            verdict="LANDED",
            evidence="pytest exit=0",
            from_peer="swe",
        )

        row = coord.store.get_slice("S15")
        assert row is not None
        assert row.status == "REVIEW"
        assert row.active_job is None
        assert row.verdict == "LANDED"
        assert row.evidence == "pytest exit=0"

    def test_cli_assign_stamps_default_source(self, coord_env, monkeypatch):
        team_root, spool = coord_env
        monkeypatch.setenv("BOT_COMS_DEFAULT_SOURCE", "discord:1543040481368346765")
        ctx = team_root / "context" / "S16.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("work\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        from bot_coms.headers import resolve_assign_headers

        result = coord.assign(
            slice_id="S16",
            to_peer="swe",
            title="cli default",
            assignment_path=str(ctx),
            headers=resolve_assign_headers(None),
        )
        assert result.outbound_id
        env = _read_inbox_envelope(spool, "swe")
        assert env["headers"] == {"delivery":"internal", "source": "discord:1543040481368346765"}
        row = coord.store.get_slice("S16")
        assert row is not None
        assert row.notify_source == "discord:1543040481368346765"

    def test_cli_assign_stamps_default_source_from_profile_env(
        self, coord_env, tmp_path, monkeypatch
    ):
        team_root, spool = coord_env
        monkeypatch.delenv("BOT_COMS_DEFAULT_SOURCE", raising=False)
        peers_yaml = team_root / "peers.yaml"
        peers_yaml.write_text(
            "peers:\n  - id: pm\n    profile: project-manager\n",
            encoding="utf-8",
        )
        profile_env = tmp_path / ".hermes" / "profiles" / "project-manager" / ".env"
        profile_env.parent.mkdir(parents=True)
        profile_env.write_text(
            "BOT_COMS_DEFAULT_SOURCE=discord:profile-file-only\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("BOT_COMS_PEERS_YAML", str(peers_yaml))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")

        ctx = team_root / "context" / "S16b.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("work\n", encoding="utf-8")

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        from bot_coms.headers import resolve_assign_headers

        result = coord.assign(
            slice_id="S16b",
            to_peer="swe",
            title="cli default file",
            assignment_path=str(ctx),
            headers=resolve_assign_headers(None, peer_id="pm"),
        )
        assert result.outbound_id
        env = _read_inbox_envelope(spool, "swe")
        assert env["headers"] == {"delivery":"internal", "source": "discord:profile-file-only"}
        row = coord.store.get_slice("S16b")
        assert row is not None
        assert row.notify_source == "discord:profile-file-only"

    def test_notify_only_after_owner_acceptance(self, coord_env, monkeypatch):
        from unittest.mock import patch
        team_root, spool = coord_env
        ctx = team_root / 'S17.md'
        ctx.write_text('work')
        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.assign(slice_id='S17',to_peer='swe',title='acceptance',assignment_path=str(ctx),headers={'source':'discord:channel-notify'})
        sent=[]
        monkeypatch.setenv('BOT_COMS_DOORBELL','1')
        with patch('bot_coms.doorbell._wake_runner',lambda *args:None), patch('bot_coms.doorbell._send_runner',lambda profile,source,env:sent.append((source,env.payload))):
            coord.report(slice_id='S17',verdict='SUBMITTED',evidence='checks and artifact',from_peer='swe')
            assert sent==[]
            coord.workflow('accept',actor='pm',slice_id='S17',revision=1,evidence='Accepted: checks passed, artifact reviewed')
        assert sent==[('discord:channel-notify',{'intent':'ack','slice':'S17','verdict':'ACCEPTED','evidence':'Accepted: checks passed, artifact reviewed'})]

    def test_stale_assign_failure_notifies_pm_with_source(self, coord_env):
        team_root, spool = coord_env
        ctx = team_root / "context" / "S14.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("## Assignment\noriginal\n\n## Notes\n", encoding="utf-8")
        digest = _digest(ctx)

        coord = TeamCoordinator(team_root=team_root, spool_root=spool)
        coord.register_slice(
            slice_id="S14",
            title="stale spec",
            assignment_path=str(ctx),
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
            notify_source="discord:channel-fail",
        )
        ctx.write_text("## Assignment\nTAMPERED\n\n## Notes\n", encoding="utf-8")

        payload = SlicePayload(schema_version="1.0", intent="assign", slice="S14")
        pm = Client(spool, "pm")
        pm.send(
            "swe",
            "event",
            payload.to_dict(),
            correlation_id="S14",
            idempotency_key=payload.idempotency_key(digest),
            reply_to="pm",
            headers={"source": "discord:channel-fail"},
        )
        pm.close()

        result = coord.process_inbox("swe", limit=1)
        assert len(result.decisions) == 1
        d = result.decisions[0]
        assert d.disposition == "fail"
        assert d.error_code == "STALE_ASSIGNMENT"

        result_env = read_json(spool / "swe" / "results" / "S14.json")
        assert result_env["headers"] == {"source": "discord:channel-fail"}
        assert result_env["payload"]["intent"] == "fail"
        assert result_env["payload"]["code"] == "STALE_ASSIGNMENT"
