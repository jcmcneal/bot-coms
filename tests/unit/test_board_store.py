"""Tests for bus.sqlite store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot_coms_board.store import BusStore, bus_db_path, default_team_root, team_root_from_env


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> BusStore:
    team_root = tmp_path / ".hermes" / "team"
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    return BusStore(bus_db_path(team_root=team_root))


class TestTeamRoot:
    def test_default_team_root_is_canonical(self):
        assert default_team_root() == Path.home() / ".hermes" / "team"

    def test_team_root_from_env(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(tmp_path / "team"))
        assert team_root_from_env() == tmp_path / "team"

    def test_no_nested_hermes_when_team_root_explicit(self, tmp_path: Path):
        """Passing ~/.hermes as team_root must not create ~/.hermes/.hermes/team."""
        team_root = tmp_path / ".hermes" / "team"
        db = bus_db_path(team_root=team_root)
        assert db == team_root / "bus.sqlite"
        assert ".hermes/.hermes" not in str(db)


class TestBusStore:
    def test_register_and_get(self, store: BusStore, tmp_path: Path):
        ctx = tmp_path / "S9.md"
        ctx.write_text("# S9\n", encoding="utf-8")
        row = store.register_slice(
            slice_id="S9",
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
            title="phase 1",
            assignment_path=str(ctx),
            content_sha256="abc",
            tags=["NOCOMMIT"],
        )
        assert row.status == "QUEUED"
        assert row.tags == ["NOCOMMIT"]
        got = store.get_slice("S9")
        assert got is not None
        assert got.title == "phase 1"

    def test_status_and_verdict(self, store: BusStore, tmp_path: Path):
        ctx = tmp_path / "S9.md"
        ctx.write_text("x", encoding="utf-8")
        store.register_slice(
            slice_id="S9",
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
            title="t",
            assignment_path=str(ctx),
            content_sha256=None,
        )
        store.set_status("S9", "RUNNING", active_job="s9-job")
        store.set_verdict("S9", verdict="LANDED", evidence="tee ok")
        row = store.get_slice("S9")
        assert row is not None
        assert row.status == "REVIEW"
        assert row.active_job is None
        assert row.verdict == "LANDED"

    def test_status_queued_clears_active_job(self, store: BusStore, tmp_path: Path):
        ctx = tmp_path / "S10.md"
        ctx.write_text("x", encoding="utf-8")
        store.register_slice(
            slice_id="S10",
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
            title="queued clears active",
            assignment_path=str(ctx),
            content_sha256=None,
        )
        store.set_status("S10", "RUNNING", active_job="s10-job")
        store.set_status("S10", "QUEUED")
        row = store.get_slice("S10")
        assert row is not None
        assert row.status == "QUEUED"
        assert row.active_job is None

    def test_log(self, store: BusStore):
        entry = store.append_log("project-manager", "dispatch verified")
        assert entry["actor"] == "project-manager"
        logs = store.list_log(limit=5)
        assert len(logs) == 1


class TestTeamBusTool:
    def test_register_action(self, tmp_path: Path, monkeypatch):
        from bot_coms_board import tool as tbt

        team_root = tmp_path / ".hermes" / "team"
        monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
        ctx = team_root / "context" / "S9.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("# assign\n", encoding="utf-8")
        raw = tbt.handle_team_bus(
            {
                "action": "register",
                "slice": "S9",
                "title": "one line",
                "assignment_path": str(ctx),
                "to_profile": "software-engineer",
                "peer": "swe",
            },
            actor="project-manager",
        )
        out = json.loads(raw)
        assert out["success"] is True
        assert out["row"]["content_sha256"]

    def test_slice_action(self, tmp_path: Path, monkeypatch):
        from bot_coms_board import tool as tbt

        team_root = tmp_path / ".hermes" / "team"
        monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
        ctx = team_root / "context" / "S9.md"
        ctx.parent.mkdir(parents=True)
        ctx.write_text("# assign\n", encoding="utf-8")
        tbt.handle_team_bus(
            {
                "action": "register",
                "slice": "S9",
                "title": "one line",
                "assignment_path": str(ctx),
            },
            actor="project-manager",
        )
        raw = tbt.handle_team_bus({"action": "slice", "slice": "S9"})
        out = json.loads(raw)
        assert out["success"] is True
        assert out["found"] is True
        assert out["slice_row"]["id"] == "S9"

    def test_list_action(self, tmp_path: Path, monkeypatch):
        from bot_coms_board import tool as tbt

        team_root = tmp_path / ".hermes" / "team"
        monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
        ctx = tmp_path / "ctx.md"
        ctx.write_text("x", encoding="utf-8")
        tbt.handle_team_bus(
            {
                "action": "register",
                "slice": "S9",
                "title": "t",
                "assignment_path": str(ctx),
            },
            actor="project-manager",
        )
        raw = tbt.handle_team_bus({"action": "list"})
        out = json.loads(raw)
        assert out["count"] == 1

    def test_home_param_rejected(self, tmp_path: Path):
        from bot_coms_board import tool as tbt

        raw = tbt.handle_team_bus({"action": "list", "home": str(tmp_path)})
        out = json.loads(raw)
        assert out["success"] is False

    def test_legacy_lock_actions_removed(self, tmp_path: Path):
        from bot_coms_board import tool as tbt

        raw = tbt.handle_team_bus({"action": "lock"}, actor="software-engineer")
        out = json.loads(raw)
        assert out["success"] is False
