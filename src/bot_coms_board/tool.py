"""Hermes team_bus tool — SQLite ledger at ``~/.hermes/team/bus.sqlite``."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from bot_coms_board.coordinator import default_spool_root
from bot_coms_board.slice_status import merge_slice_view
from bot_coms_board.store import open_store, profile_to_peer, team_root_from_env, utc_iso
from bot_coms_board.payload import PayloadError, parse_payload

_ACTIONS = frozenset(
    {
        "register",
        "status",
        "verdict",
        "log",
        "slice",
        "list",
        "read",
    }
)
_STATUSES = frozenset(
    {
        "BACKLOG",
        "QUEUED",
        "RUNNING",
        "BLOCKED",
        "REVIEW",
        "DONE",
        "PARKED",
    }
)
_SLICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

TEAM_BUS_SCHEMA = {
    "name": "team_bus",
    "description": (
        "Team coordination ledger (~/.hermes/team/bus.sqlite). "
        "Register slices before bot-coms ping. Actions: register, status, verdict, "
        "log, slice, list, read (alias list)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_ACTIONS),
            },
            "slice": {"type": "string", "description": "Slice id"},
            "to_profile": {"type": "string"},
            "from_profile": {"type": "string"},
            "peer": {"type": "string", "description": "bot-coms peer id (swe, verifier, …)"},
            "title": {"type": "string"},
            "assignment_path": {"type": "string"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "status": {
                "type": "string",
                "enum": sorted(_STATUSES),
            },
            "verdict": {"type": "string"},
            "evidence": {"type": "string"},
            "message": {"type": "string", "description": "log body (single line)"},
            "active_job": {"type": "string"},
            "include_log": {"type": "integer"},
            "limit": {"type": "integer"},
            "spool_root": {"type": "string"},
        },
        "required": ["action"],
    },
}


def tool_error(message, **extra) -> str:
    result = {"error": str(message), "success": False}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(**kwargs) -> str:
    data = {"success": True, **kwargs}
    return json.dumps(data, ensure_ascii=False)


def resolve_active_profile() -> Optional[str]:
    try:
        from hermes_constants import get_hermes_home
        from gateway.status import _profile_label_for_home

        label = _profile_label_for_home(get_hermes_home())
    except Exception:
        return None
    if isinstance(label, str) and _PROFILE_RE.fullmatch(label):
        return label
    return None


def _err(message: str, **extra: Any) -> str:
    return tool_error(message, **extra)


def _validate_slice(slice_id: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(slice_id, str) or not _SLICE_RE.fullmatch(slice_id):
        return None, _err(
            "invalid slice: must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
            field="slice",
        )
    return slice_id, None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spool_root(args: dict) -> Path:
    raw = args.get("spool_root")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return default_spool_root()


def _action_register(args: dict, actor: str) -> str:
    sid, err = _validate_slice(args.get("slice"))
    if err:
        return err
    title = args.get("title")
    assignment_path = args.get("assignment_path")
    if not isinstance(title, str) or not title.strip():
        return _err("register requires title", field="title")
    if not isinstance(assignment_path, str) or not assignment_path.strip():
        return _err("register requires assignment_path", field="assignment_path")
    path = Path(assignment_path).expanduser()
    if not path.is_file():
        return _err(f"assignment file not found: {path}", field="assignment_path")
    to_profile = args.get("to_profile")
    if not isinstance(to_profile, str) or not to_profile.strip():
        to_profile = "software-engineer"
    from_profile = args.get("from_profile")
    if not isinstance(from_profile, str) or not from_profile.strip():
        from_profile = actor
    peer = args.get("peer")
    if not isinstance(peer, str) or not peer.strip():
        peer = profile_to_peer(to_profile)
    tags = args.get("tags")
    tag_list: list[str] = []
    if isinstance(tags, list):
        tag_list = [str(t) for t in tags]
    digest = _sha256_file(path)
    store = open_store()
    row = store.register_slice(
        slice_id=sid,  # type: ignore[arg-type]
        to_profile=to_profile.strip(),
        from_profile=from_profile.strip(),
        peer=peer.strip(),
        title=title.strip(),
        assignment_path=str(path.resolve()),
        content_sha256=digest,
        tags=tag_list,
        status="QUEUED",
    )
    return tool_result(action="register", slice=sid, row=row.to_dict(), actor=actor)


def _action_status(args: dict, actor: str) -> str:
    sid, err = _validate_slice(args.get("slice"))
    if err:
        return err
    status = args.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        return _err(f"invalid status: must be one of {sorted(_STATUSES)}", field="status")
    active_job = args.get("active_job")
    job = active_job.strip() if isinstance(active_job, str) and active_job.strip() else None
    if job and not _JOB_RE.fullmatch(job):
        return _err("invalid active_job", field="active_job")
    store = open_store()
    row = store.set_status(sid, status, active_job=job)  # type: ignore[arg-type]
    if row is None:
        return _err(f"slice not found: {sid}", slice=sid)
    return tool_result(action="status", slice=sid, status=status, row=row.to_dict(), actor=actor)


def _action_verdict(args: dict, actor: str) -> str:
    sid, err = _validate_slice(args.get("slice"))
    if err:
        return err
    verdict = args.get("verdict")
    evidence = args.get("evidence")
    if not (isinstance(verdict, str) and verdict.strip()) and not (
        isinstance(evidence, str) and evidence.strip()
    ):
        return _err("verdict action requires verdict and/or evidence")
    store = open_store()
    row = store.set_verdict(
        sid,  # type: ignore[arg-type]
        verdict=verdict.strip() if isinstance(verdict, str) else None,
        evidence=evidence.strip() if isinstance(evidence, str) else None,
    )
    if row is None:
        return _err(f"slice not found: {sid}", slice=sid)
    return tool_result(action="verdict", slice=sid, row=row.to_dict(), actor=actor)


def _action_log(args: dict, actor: str) -> str:
    message = args.get("message")
    if not isinstance(message, str) or not message.strip():
        return _err("log action requires message", field="message")
    store = open_store()
    try:
        entry = store.append_log(actor, message)
    except ValueError as exc:
        return _err(str(exc), field="message")
    return tool_result(action="log", entry=entry, actor=actor)


def _action_slice(args: dict) -> str:
    sid, err = _validate_slice(args.get("slice"))
    if err:
        return err
    store = open_store()
    row = store.get_slice(sid)  # type: ignore[arg-type]
    view = merge_slice_view(row, slice_id=sid, spool_root=_spool_root(args))
    include_log = int(args.get("include_log") or 0)
    if include_log > 0:
        view["log"] = store.list_log(limit=include_log)
    return tool_result(action="slice", **view)


def _action_list(args: dict) -> str:
    limit = int(args.get("limit") or 100)
    store = open_store()
    rows = store.list_slices(limit=limit)
    spool = _spool_root(args)
    slices = [merge_slice_view(r, slice_id=r.id, spool_root=spool) for r in rows]
    include_log = int(args.get("include_log") or 0)
    log_lines: list[dict[str, Any]] = []
    if include_log > 0:
        log_lines = store.list_log(limit=include_log)
    hold = store.get_setting("global_hold")
    return tool_result(
        action="list",
        slices=slices,
        log=log_lines,
        global_hold=hold,
        count=len(slices),
    )


def handle_team_bus(args: dict, **kw: Any) -> str:
    action = args.get("action")
    if not isinstance(action, str) or action not in _ACTIONS:
        return _err(f"invalid action: must be one of {sorted(_ACTIONS)}", field="action")
    if "actor" in args or "profile" in args:
        return _err("actor/profile must not be passed as args; stamped from HERMES_HOME", field="actor")
    if "home" in args:
        return _err("home is not supported; board uses ~/.hermes/team (BOT_COMS_TEAM_ROOT for tests)", field="home")

    actor = kw.get("actor") or resolve_active_profile() or "unknown"

    if action == "read":
        action = "list"
    if action == "register":
        if actor == "unknown":
            return _err("cannot resolve active profile from HERMES_HOME")
        return _action_register(args, actor)
    if action == "status":
        return _action_status(args, actor)
    if action == "verdict":
        return _action_verdict(args, actor)
    if action == "log":
        return _action_log(args, actor)
    if action == "slice":
        return _action_slice(args)
    if action == "list":
        return _action_list(args)
    return _err(f"unknown action: {action}")


def parse_team_payload(raw: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    """Helper for adapters: parse payload or return error JSON string."""
    try:
        p = parse_payload(raw)
        return p.to_dict(), None
    except PayloadError as exc:
        return None, tool_error(str(exc), code=exc.code)
