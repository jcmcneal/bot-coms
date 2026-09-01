"""Tests for slice_status spool join."""

from __future__ import annotations

import json
from pathlib import Path

from bot_coms_board.slice_status import (
    COMMS_INBOX,
    COMMS_RESPONDED,
    comms_state_for_slice,
    merge_slice_view,
)
from bot_coms_board.store import BusStore, bus_db_path


def _write_env(path: Path, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields), encoding="utf-8")


class TestSliceStatus:
    def test_comms_inbox(self, tmp_path: Path):
        spool = tmp_path / "spool"
        _write_env(
            spool / "swe" / "inbox" / "01MSG.json",
            id="01MSG",
            correlation_id="S9",
            type="request",
            payload={"intent": "assign", "slice": "S9"},
        )
        state, detail = comms_state_for_slice("S9", spool_root=spool, peer="swe")
        assert state == COMMS_INBOX
        assert detail["message_id"] == "01MSG"

    def test_comms_responded(self, tmp_path: Path):
        spool = tmp_path / "spool"
        _write_env(
            spool / "pm" / "results" / "S9.json",
            correlation_id="S9",
            type="response",
            payload={"intent": "ack", "slice": "S9", "status": "RUNNING"},
        )
        state, _ = comms_state_for_slice("S9", spool_root=spool, peer="swe")
        assert state == COMMS_RESPONDED

    def test_merge_slice_view(self, tmp_path: Path):
        store = BusStore(bus_db_path(team_root=tmp_path))
        ctx = tmp_path / "ctx.md"
        ctx.write_text("body", encoding="utf-8")
        row = store.register_slice(
            slice_id="S9",
            to_profile="software-engineer",
            from_profile="project-manager",
            peer="swe",
            title="t",
            assignment_path=str(ctx),
            content_sha256="x",
        )
        spool = tmp_path / "spool"
        _write_env(
            spool / "swe" / "inbox" / "01MSG.json",
            correlation_id="S9",
            type="request",
        )
        view = merge_slice_view(row, slice_id="S9", spool_root=spool)
        assert view["found"] is True
        assert view["comms_state"] == COMMS_INBOX
        assert view["slice_row"]["id"] == "S9"
