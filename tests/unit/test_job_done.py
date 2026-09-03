"""bot-coms job-done: sidecar + peers.yaml → team_report (no role allowlist)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot_coms.spool import init_spool
from bot_coms_board.coordinator import TeamCoordinator
from bot_coms_board.job_done import (
    job_done,
    parse_exit_code,
    resolve_worker_peer,
    verdict_for_exit,
)


@pytest.fixture
def job_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    team_root = home / ".hermes" / "team"
    spool = team_root / "spool"
    init_spool(spool, ["pm", "swe", "ux", "em"])
    peers = team_root / "peers.yaml"
    peers.write_text(
        "peers:\n"
        "  - id: pm\n    profile: project-manager\n"
        "  - id: swe\n    profile: software-engineer\n"
        "  - id: ux\n    profile: ux-designer\n"
        "  - id: em\n    profile: engineering-manager\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(spool))
    monkeypatch.setenv("BOT_COMS_PEERS_YAML", str(peers))
    monkeypatch.setenv("BOT_COMS_DOORBELL", "0")
    monkeypatch.setenv("HOME", str(home))
    return home, team_root, spool


def _write_sidecar(home: Path, job: str, **fields) -> Path:
    path = home / ".hermes" / "cursor-screen" / f"{job}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def _write_tee(home: Path, job: str, exit_code: str = "0") -> Path:
    tee = home / ".hermes" / f"{job}.out"
    tee.parent.mkdir(parents=True, exist_ok=True)
    tee.write_text(
        f'{{"session_id": "sess-{job}"}}\nEXIT:{exit_code}\n',
        encoding="utf-8",
    )
    return tee


def test_verdict_for_exit() -> None:
    assert verdict_for_exit("0") == "LANDED"
    assert verdict_for_exit("0", "write") == "LANDED"
    assert verdict_for_exit("0", "force") == "LANDED"
    assert verdict_for_exit("0", "ask") == "ASK_DONE"
    assert verdict_for_exit("0", "plan") == "PLAN_DONE"
    assert verdict_for_exit("unknown") == "LANDED"
    assert verdict_for_exit("1") == "FAIL"
    assert verdict_for_exit("1", "ask") == "FAIL"
    assert verdict_for_exit("orphaned") == "FAIL"


def test_parse_exit_code_last_line(tmp_path: Path) -> None:
    tee = tmp_path / "j.out"
    tee.write_text("EXIT:1\nstuff\nEXIT:0\n", encoding="utf-8")
    assert parse_exit_code(tee) == "0"


def test_resolve_worker_peer_uses_peers_yaml(job_env) -> None:
    assert resolve_worker_peer("ux-designer") == "ux"
    assert resolve_worker_peer("engineering-manager") == "em"
    assert resolve_worker_peer("software-engineer") == "swe"


def test_job_done_reports_any_roster_profile(job_env, monkeypatch) -> None:
    home, team_root, spool = job_env
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")
    ctx = team_root / "context" / "S200.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("ux work\n", encoding="utf-8")
    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S200",
        to_peer="ux",
        title="ux-slice",
        assignment_path=str(ctx),
        from_peer="pm",
        from_profile="project-manager",
        to_profile="ux-designer",
    )

    _write_sidecar(
        home,
        "ux-job",
        profile="ux-designer",
        slice="S200",
        mode="force",
    )
    _write_tee(home, "ux-job", "0")

    out = job_done("ux-job", home=home, team_root=team_root, spool_root=spool)
    assert out["success"] is True
    assert out["action"] == "report"
    assert out["peer"] == "ux"
    assert out["verdict"] == "LANDED"
    assert out["report"]["to_peer"] == "pm"
    assert list((spool / "pm" / "inbox").glob("*.json"))


def test_job_done_reports_write_mode(job_env, monkeypatch) -> None:
    home, team_root, spool = job_env
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")
    ctx = team_root / "context" / "S1.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("write work\n", encoding="utf-8")
    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S1",
        to_peer="swe",
        title="write-slice",
        assignment_path=str(ctx),
        from_peer="pm",
    )
    _write_sidecar(home, "w1", profile="software-engineer", slice="S1", mode="write")
    _write_tee(home, "w1", "0")
    out = job_done("w1", home=home, team_root=team_root, spool_root=spool)
    assert out["action"] == "report"
    assert out["success"] is True
    assert out["verdict"] == "LANDED"
    assert list((spool / "pm" / "inbox").glob("*.json"))


def test_job_done_fail_exit(job_env, monkeypatch) -> None:
    home, team_root, spool = job_env
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")
    ctx = team_root / "context" / "S201.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("swe work\n", encoding="utf-8")
    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S201",
        to_peer="swe",
        title="swe-slice",
        assignment_path=str(ctx),
        from_peer="pm",
    )
    _write_sidecar(home, "swe-job", profile="software-engineer", slice="S201", mode="force")
    _write_tee(home, "swe-job", "2")
    out = job_done("swe-job", home=home, team_root=team_root, spool_root=spool)
    assert out["verdict"] == "FAIL"
    assert out["action"] == "report"


def test_job_done_ask_mode_reports_without_landing(job_env, monkeypatch) -> None:
    home, team_root, spool = job_env
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")
    ctx = team_root / "context" / "S9.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("ask work\n", encoding="utf-8")
    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S9",
        to_peer="ux",
        title="ask-slice",
        assignment_path=str(ctx),
        from_peer="pm",
    )
    _write_sidecar(home, "ask1", profile="ux-designer", slice="S9", mode="ask")
    _write_tee(home, "ask1", "0")
    out = job_done("ask1", home=home, team_root=team_root, spool_root=spool)
    assert out["action"] == "report"
    assert out["verdict"] == "ASK_DONE"
    assert out["success"] is True
    assert list((spool / "pm" / "inbox").glob("*.json"))
    row = coord.store.get_slice("S9")
    assert row is not None
    assert row.verdict == "ASK_DONE"
    assert row.status == "QUEUED"


def test_job_done_personal_csa_notify(job_env, monkeypatch, tmp_path) -> None:
    home, team_root, spool = job_env
    ping = tmp_path / "ping-csa.sh"
    ping.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    ping.chmod(0o755)
    monkeypatch.setenv("BOT_COMS_CSA_PING", str(ping))
    _write_sidecar(home, "cs-lay-of-land", profile="cyber-security", mode="ask")
    _write_tee(home, "cs-lay-of-land", "0")
    out = job_done("cs-lay-of-land", home=home, team_root=team_root, spool_root=spool)
    assert out["action"] == "notify_adapter"
    assert out["source"] == "csa:csa"
    assert list((spool / "pm" / "inbox").glob("*.json")) == []
