"""Hermes tools for bot-coms board (team coordination lane)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from bot_coms.headers import format_source, resolve_assign_headers
from bot_coms_board.coordinator import TeamCoordinator, default_spool_root
from bot_coms_board.tool import TEAM_BUS_SCHEMA, handle_team_bus


def _args(args: dict[str, Any] | None, kwargs: dict[str, Any]) -> dict[str, Any]:
    if args:
        return args
    return {k: v for k, v in kwargs.items() if k != "ctx"}


def _json_result(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def team_bus(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    return handle_team_bus(a)


TEAM_ASSIGN_SCHEMA = {
    "name": "team_assign",
    "description": (
        "Assigner dispatch: register slice in bus.sqlite and send lean bot-coms ping "
        "(fire-and-forget). Doorbell wakes ``to``; report returns to envelope ``from``. "
        "RUNNING ack does not wake — LANDED/FAIL arrive via team_report."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "slice": {"type": "string"},
            "to_peer": {"type": "string", "description": "swe, verifier, dna-researcher"},
            "title": {"type": "string"},
            "assignment_path": {"type": "string"},
            "to_profile": {"type": "string"},
            "from_profile": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "intent": {"type": "string", "enum": ["assign", "report_only"]},
            "headers": {
                "type": "object",
                "description": "Opaque routing headers; source stamps the originating channel",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["slice", "to_peer", "title", "assignment_path"],
    },
}


def _session_source() -> str:
    """Auto-stamp from Hermes session context when running inside the gateway."""
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return ""
    platform = (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower()
    if platform in {"", "cli", "tui", "local"}:
        return ""
    chat_id = (get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip()
    if not chat_id:
        return ""
    thread_id = (get_session_env("HERMES_SESSION_THREAD_ID", "") or "").strip()
    return format_source(platform, chat_id, thread_id)


def _resolve_assign_headers(a: dict[str, Any]) -> dict[str, str] | None:
    raw = a.get("headers")
    headers: dict[str, str] | None = None
    if isinstance(raw, dict):
        headers = {str(k): str(v) for k, v in raw.items()}
    return resolve_assign_headers(headers, session_source=_session_source())


def team_assign(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    coord = TeamCoordinator()
    from_peer = os.environ.get("BOT_COMS_PEER_ID") or None
    try:
        result = coord.assign(
            slice_id=a["slice"],
            to_peer=a["to_peer"],
            title=a["title"],
            assignment_path=a["assignment_path"],
            from_profile=a.get("from_profile") or "project-manager",
            from_peer=from_peer,
            to_profile=a.get("to_profile"),
            tags=a.get("tags"),
            intent=a.get("intent") or "assign",
            headers=_resolve_assign_headers(a),
        )
    except Exception as exc:
        return _json_result({"success": False, "error": str(exc)})
    return _json_result(
        {
            "success": True,
            "slice": result.slice_id,
            "row": result.row,
            "outbound_id": result.outbound_id,
            "correlation_id": result.correlation_id,
        }
    )


TEAM_INBOX_SCHEMA = {
    "name": "team_inbox",
    "description": (
        "Process bot-coms inbox for this peer: claim, validate payload, join SQL, "
        "verify digest, auto-handle report_only/cancel/fail. Returns dispatch "
        "decisions (assign → launch_cursor bundle). Assign stays claimed until "
        "bot_coms_ack after cursor_screen + team_bus status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "spool_root": {"type": "string"},
        },
    },
}


def team_inbox(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    peer = os.environ.get("BOT_COMS_PEER_ID")
    if not peer:
        return _json_result({"success": False, "error": "BOT_COMS_PEER_ID required"})
    spool_raw = a.get("spool_root")
    spool = Path(spool_raw).expanduser() if isinstance(spool_raw, str) and spool_raw else default_spool_root()
    coord = TeamCoordinator(spool_root=spool)
    result = coord.process_inbox(
        peer,
        limit=int(a.get("limit") or 10),
        auto_handle=False,
        auto_handle_intents=frozenset({"report_only", "report", "cancel"}),
    )
    return _json_result(
        {
            "success": True,
            "peer": result.peer,
            "reclaimed": result.reclaimed,
            "decisions": [
                {
                    "message_id": d.message_id,
                    "slice": d.slice_id,
                    "intent": d.intent,
                    "disposition": d.disposition,
                    "handled": d.handled,
                    "assignment_path": d.assignment_path,
                    "assignment_body": d.assignment_body,
                    "slice_row": d.slice_row,
                    "ack_result": d.ack_result,
                    "error": d.error,
                    "error_code": d.error_code,
                }
                for d in result.decisions
            ],
        }
    )


TEAM_REPORT_SCHEMA = {
    "name": "team_report",
    "description": (
        "Stamp verdict in bus.sqlite and emit lean {intent:report} doorbell "
        "to whoever assigned (envelope return address / from_profile peer)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "slice": {"type": "string"},
            "verdict": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["slice"],
    },
}


def team_report(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    peer = os.environ.get("BOT_COMS_PEER_ID")
    if not peer:
        return _json_result({"success": False, "error": "BOT_COMS_PEER_ID required"})
    coord = TeamCoordinator()
    try:
        out = coord.report(
            slice_id=a["slice"],
            verdict=a.get("verdict"),
            evidence=a.get("evidence"),
            from_peer=peer,
        )
    except Exception as exc:
        return _json_result({"success": False, "error": str(exc)})
    return _json_result({"success": True, **out})
