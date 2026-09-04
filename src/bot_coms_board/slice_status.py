"""Join SQL slice rows with bot-coms spool comms state."""

from __future__ import annotations

import json
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
