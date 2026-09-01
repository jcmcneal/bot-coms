"""Tests for BUS.md → SQLite migration."""

from __future__ import annotations

from pathlib import Path

from bot_coms_board.migrate import migrate_bus_md, parse_current_slices
from bot_coms_board.store import open_store

SAMPLE = """# Hermes team bus

LOCK: none

## Current

S9 to: software-engineer from: project-manager [NOCOMMIT] QUEUED — phase 1 spike
Assignment: /Users/jason/.hermes/team/context/S9.md
Evidence: none yet
Ack: none
Verdict: none

## Log

- 2026-08-31T13:55:00Z — S9 ACTIVE NEXT (actor=project-manager)
"""


class TestMigrate:
    def test_parse_current(self):
        slices = parse_current_slices(SAMPLE.split("## Current")[1].split("## Log")[0])
        assert len(slices) == 1
        assert slices[0]["id"] == "S9"
        assert slices[0]["status"] == "QUEUED"

    def test_migrate_dry_run(self, tmp_path: Path):
        bus = tmp_path / "BUS.md"
        bus.write_text(SAMPLE, encoding="utf-8")
        report = migrate_bus_md(bus, team_root=tmp_path / ".hermes" / "team", dry_run=True)
        assert report["slices_found"] == 1

    def test_migrate_import(self, tmp_path: Path):
        bus = tmp_path / ".hermes" / "team" / "BUS.md"
        bus.parent.mkdir(parents=True)
        bus.write_text(SAMPLE, encoding="utf-8")
        report = migrate_bus_md(bus, team_root=tmp_path / ".hermes" / "team", dry_run=False)
        assert report["imported_slices"] == 1
        store = open_store(team_root=tmp_path / ".hermes" / "team")
        row = store.get_slice("S9")
        assert row is not None
        assert row.title == "phase 1 spike"
