"""Tests for slice_status spool join."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot_coms_board.slice_status import (
    COMMS_INBOX,
    COMMS_RESPONDED,
    checklist_summary,
    comms_state_for_slice,
    merge_slice_view,
)
from bot_coms_board.store import BusStore, SliceRow, bus_db_path


def _write_env(path: Path, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields), encoding="utf-8")


def _slice_row(
    tmp_path: Path,
    assignment: str,
    *,
    status: str = "QUEUED",
    active_job: str | None = None,
) -> SliceRow:
    context = tmp_path / "assignment.md"
    context.write_text(assignment, encoding="utf-8")
    store = BusStore(bus_db_path(team_root=tmp_path))
    row = store.register_slice(
        slice_id="CHECKLIST",
        to_profile="software-engineer",
        from_profile="project-manager",
        peer="swe",
        title="checklist",
        assignment_path=str(context),
        content_sha256="x",
        status=status if active_job is None else "QUEUED",
    )
    if active_job is not None:
        row = store.set_status("CHECKLIST", "RUNNING", active_job=active_job)
        assert row is not None
    store.close()
    return row


UNFINISHED_CHECKLIST = """\
## Completion checklist

| ID | Responsible peer | Completion condition | Required evidence |
|---|---|---|---|
| C1 | swe | first outcome | first evidence |
| C2 | swe | second outcome | second evidence |

## Notes

### Checklist result

| ID | Status | Evidence or blocker |
|---|---|---|
| C1 | satisfied | complete |
| C2 | not attempted | still due |
"""


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

    def test_unfinished_checklist_without_job_is_open_incomplete(self, tmp_path: Path):
        row = _slice_row(tmp_path, UNFINISHED_CHECKLIST)

        view = merge_slice_view(row, slice_id=row.id, spool_root=tmp_path / "spool")

        assert view["open_incomplete"] is True
        assert view["incomplete_reason"] == "unfinished_checklist_no_live_job"
        assert view["checklist_summary"] == {
            "present": True,
            "total": 2,
            "satisfied": 1,
            "blocked": 0,
            "not_attempted": 1,
            "dispositioned": 2,
            "unfinished": 1,
        }

    def test_completed_checklist_is_not_open_incomplete(self, tmp_path: Path):
        row = _slice_row(
            tmp_path,
            UNFINISHED_CHECKLIST.replace(
                "| C2 | not attempted | still due |",
                "| C2 | satisfied | complete |",
            ),
        )

        view = merge_slice_view(row, slice_id=row.id, spool_root=tmp_path / "spool")

        assert view["open_incomplete"] is False
        assert view["incomplete_reason"] is None
        assert view["checklist_summary"]["unfinished"] == 0

    def test_live_job_suppresses_open_incomplete(self, tmp_path: Path):
        row = _slice_row(tmp_path, UNFINISHED_CHECKLIST, active_job="job-live")

        view = merge_slice_view(row, slice_id=row.id, spool_root=tmp_path / "spool")

        assert view["open_incomplete"] is False
        assert view["incomplete_reason"] is None

    @pytest.mark.parametrize("status", ["DONE", "CANCELLED"])
    def test_terminal_status_ignores_unfinished_checklist(
        self, tmp_path: Path, status: str
    ):
        row = _slice_row(tmp_path, UNFINISHED_CHECKLIST, status=status)

        view = merge_slice_view(row, slice_id=row.id, spool_root=tmp_path / "spool")

        assert view["open_incomplete"] is False
        assert view["incomplete_reason"] is None

    def test_loose_checklist_rows_are_parsed(self, tmp_path: Path):
        assignment = tmp_path / "loose.md"
        assignment.write_text(
            """\
## Completion checklist
R1 | swe | First condition | First evidence | parent M1.
R2 | swe | Second condition | Second evidence | parent M2.

## Notes
### Checklist result
R1 | satisfied | complete | none
R2 | blocked | waiting | retry / swe
""",
            encoding="utf-8",
        )

        assert checklist_summary(str(assignment)) == {
            "present": True,
            "total": 2,
            "satisfied": 1,
            "blocked": 1,
            "not_attempted": 0,
            "dispositioned": 2,
            "unfinished": 1,
        }

    def test_unparseable_checklist_uses_open_status_proxy(self, tmp_path: Path):
        row = _slice_row(tmp_path, "## Completion checklist\n\nprose only\n")

        view = merge_slice_view(row, slice_id=row.id, spool_root=tmp_path / "spool")

        assert view["open_incomplete"] is True
        assert view["incomplete_reason"] == "open_status_no_live_job"
        assert "checklist_summary" not in view
