"""Join SQL slice rows with bot-coms spool comms state."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bot_coms_board.store import SliceRow

DEFAULT_SPOOL_ROOT = Path.home() / ".hermes" / "team" / "spool"

COMMS_UNDELIVERED = "undelivered"
COMMS_INBOX = "inbox"
COMMS_PROCESSING = "processing"
COMMS_ACKED = "acked"
COMMS_RESPONDED = "responded"
COMMS_UNKNOWN = "unknown"

OPEN_STATUSES = frozenset({"QUEUED", "BLOCKED", "RUNNING", "REVIEW", "SUBMITTED", "ASK_DONE"})
TERMINAL_STATUSES = frozenset({"DONE", "CANCELLED"})

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DISPOSITION_RE = re.compile(r"\b(not[\s_-]+attempted|satisfied|blocked)\b", re.IGNORECASE)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _envelope_correlation(env: dict[str, Any]) -> str | None:
    corr = env.get("correlation_id")
    return corr if isinstance(corr, str) else None


def _scan_folder(folder: Path, slice_id: str) -> dict[str, Any] | None:
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        env = _read_json(path)
        if env is None:
            continue
        if _envelope_correlation(env) == slice_id:
            return {"location": folder.name, "message_id": path.stem, "envelope": env}
    return None


def comms_state_for_slice(
    slice_id: str,
    *,
    spool_root: Path | None = None,
    peer: str | None = None,
    return_peer: str = "pm",
) -> tuple[str, dict[str, Any]]:
    """Return (comms_state, detail dict) derived from spool folders only."""
    root = spool_root or DEFAULT_SPOOL_ROOT
    detail: dict[str, Any] = {"spool_root": str(root)}

    if peer:
        peer_root = root / peer
        for loc in ("results", "inbox", "processing", "acked", "dead-letter"):
            folder = peer_root / loc
            hit = _scan_folder(folder, slice_id)
            if hit:
                detail.update(hit)
                if loc == "results":
                    return COMMS_RESPONDED, detail
                if loc == "inbox":
                    return COMMS_INBOX, detail
                if loc == "processing":
                    return COMMS_PROCESSING, detail
                if loc == "acked":
                    return COMMS_ACKED, detail
                if loc == "dead-letter":
                    detail["dead_letter"] = True
                    return COMMS_UNKNOWN, detail

    pm_root = root / return_peer
    for loc in ("results", "inbox", "processing", "acked", "outbox", "dead-letter"):
        folder = pm_root / loc
        hit = _scan_folder(folder, slice_id)
        if hit:
            detail.update(hit)
            if loc == "results":
                return COMMS_RESPONDED, detail
            if loc == "outbox":
                return COMMS_UNDELIVERED, detail
            if loc == "inbox":
                return COMMS_INBOX, detail
            if loc == "processing":
                return COMMS_PROCESSING, detail
            if loc == "acked":
                return COMMS_ACKED, detail

    if peer:
        outbox = root / return_peer / "outbox"
        hit = _scan_folder(outbox, slice_id)
        if hit:
            detail.update(hit)
            return COMMS_UNDELIVERED, detail

    return COMMS_UNKNOWN, detail


def _markdown_section(body: str, title: str) -> list[str] | None:
    lines = body.splitlines()
    wanted = title.casefold()
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line.strip())
        if match is None or match.group(2).strip().casefold() != wanted:
            continue
        level = len(match.group(1))
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            next_match = _HEADING_RE.match(lines[next_index].strip())
            if next_match is not None and len(next_match.group(1)) <= level:
                end = next_index
                break
        return lines[index + 1 : end]
    return None


def _pipe_cells(line: str) -> list[str]:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return cells if len(cells) >= 2 else []


def _checklist_item_ids(lines: list[str]) -> list[str]:
    ids: list[str] = []
    for line in lines:
        cells = _pipe_cells(line)
        if len(cells) < 3:
            continue
        item_id = cells[0].strip("`*_ ")
        if (
            item_id.casefold() == "id"
            or not _ITEM_ID_RE.fullmatch(item_id)
            or all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
        ):
            continue
        if item_id not in ids:
            ids.append(item_id)
    return ids


def _checklist_dispositions(lines: list[str]) -> dict[str, str]:
    dispositions: dict[str, str] = {}
    for line in lines:
        cells = _pipe_cells(line)
        if len(cells) < 2:
            continue
        item_id = cells[0].strip("`*_ ")
        if item_id.casefold() == "id" or not _ITEM_ID_RE.fullmatch(item_id):
            continue
        match = _DISPOSITION_RE.search(cells[1])
        if match is not None:
            dispositions[item_id] = re.sub(
                r"[\s_-]+", "_", match.group(1).casefold()
            )
    return dispositions


def checklist_summary(assignment_path: str) -> dict[str, Any] | None:
    """Summarize a parseable assignment completion checklist."""
    try:
        body = Path(assignment_path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    checklist = _markdown_section(body, "Completion checklist")
    if checklist is None:
        return None
    item_ids = _checklist_item_ids(checklist)
    if not item_ids:
        return None

    result = _markdown_section(body, "Checklist result")
    dispositions = _checklist_dispositions(result or [])
    counts = {
        status: sum(dispositions.get(item_id) == status for item_id in item_ids)
        for status in ("satisfied", "blocked", "not_attempted")
    }
    satisfied = counts["satisfied"]
    return {
        "present": True,
        "total": len(item_ids),
        "satisfied": satisfied,
        "blocked": counts["blocked"],
        "not_attempted": counts["not_attempted"],
        "dispositioned": sum(item_id in dispositions for item_id in item_ids),
        "unfinished": len(item_ids) - satisfied,
    }


def incomplete_slice_view(row: SliceRow | None) -> dict[str, Any]:
    """Return durable computed fields describing open work with no live job."""
    out: dict[str, Any] = {
        "open_incomplete": False,
        "incomplete_reason": None,
    }
    if row is None:
        return out

    summary = checklist_summary(row.assignment_path)
    if summary is not None:
        out["checklist_summary"] = summary
    if row.status.upper() in TERMINAL_STATUSES or row.active_job:
        return out

    if summary is not None:
        if summary["unfinished"] > 0:
            out["open_incomplete"] = True
            out["incomplete_reason"] = "unfinished_checklist_no_live_job"
        return out

    if row.status.upper() in OPEN_STATUSES:
        out["open_incomplete"] = True
        out["incomplete_reason"] = "open_status_no_live_job"
    return out


def merge_slice_view(
    row: SliceRow | None,
    *,
    slice_id: str,
    spool_root: Path | None = None,
) -> dict[str, Any]:
    peer = row.peer if row else None
    from bot_coms_board.store import profile_to_peer
    return_peer = (row.contract.get("return_peer") or profile_to_peer(row.from_profile)) if row else "pm"
    comms, comms_detail = comms_state_for_slice(
        slice_id, spool_root=spool_root, peer=peer, return_peer=return_peer
    )
    out: dict[str, Any] = {
        "slice": slice_id,
        "found": row is not None,
        "comms_state": comms,
        "comms": comms_detail,
        **incomplete_slice_view(row),
    }
    if row is not None:
        out["slice_row"] = row.to_dict()
    result_path = None
    if peer and spool_root:
        candidate = spool_root / peer / "results" / f"{slice_id}.json"
        if candidate.is_file():
            result_path = candidate
        pm_candidate = spool_root / return_peer / "results" / f"{slice_id}.json"
        if pm_candidate.is_file():
            result_path = pm_candidate
    elif spool_root:
        for p in (return_peer, peer or ""):
            if not p:
                continue
            candidate = spool_root / p / "results" / f"{slice_id}.json"
            if candidate.is_file():
                result_path = candidate
                break
    if result_path is not None:
        env = _read_json(result_path)
        if env is not None:
            payload = env.get("payload")
            out["result_payload"] = payload if isinstance(payload, dict) else {}
    return out
