"""Hermes tool handlers → bot_coms.Client (env-configured)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from bot_coms import Client
from bot_coms.atomic import read_json
from bot_coms.call import CallDeadLetter, CallTimeout, map_call_error
from bot_coms.envelope import envelope_from_dict
from bot_coms.headers import resolve_assign_headers
from bot_coms.types import ClaimedMessage, PermissionDenied


def _client() -> Client:
    root = os.environ.get("BOT_COMS_SPOOL_ROOT")
    peer = os.environ.get("BOT_COMS_PEER_ID")
    token = os.environ.get("BOT_COMS_TOKEN")
    if not root or not peer:
        raise RuntimeError("BOT_COMS_SPOOL_ROOT and BOT_COMS_PEER_ID are required")
    return Client(Path(root), peer, token=token)


def _args(args: dict[str, Any] | None, kwargs: dict[str, Any]) -> dict[str, Any]:
    if args:
        return args
    return {k: v for k, v in kwargs.items() if k != "ctx"}


def _normalize_headers(raw: Any) -> dict[str, str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("headers must be a string map")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("headers must be a string map")
        out[key] = value
    return out or None


def _session_source() -> str:
    """Auto-stamp from Hermes session context when running inside the gateway."""
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return ""
    from bot_coms.headers import session_source_from_env

    return session_source_from_env(
        lambda k, d="": get_session_env(k, d) or "",
    )


def _resolve_send_headers(a: dict[str, Any]) -> dict[str, str] | None:
    headers = _normalize_headers(a.get("headers"))
    return resolve_assign_headers(headers, session_source=_session_source())


def bot_coms_send(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    payload = a.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    env = _client().send(
        a["to"],
        a.get("type", "request"),
        payload if isinstance(payload, dict) else {},
        idempotency_key=a.get("idempotency_key"),
        correlation_id=a.get("correlation_id"),
        reply_to=a.get("reply_to"),
        ttl_s=a.get("ttl_s"),
        headers=_resolve_send_headers(a),
    )
    return json.dumps(env.to_dict())


def bot_coms_claim(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    claimed = _client().claim(msg_id=a.get("id"))
    if claimed is None:
        return json.dumps({"claimed": False})
    return json.dumps({"claimed": True, "lease_token": claimed.lease_token, "envelope": claimed.envelope.to_dict()})


def bot_coms_reclaim(args: dict | None = None, **kwargs) -> str:
    """Sweep stale processing leases without requiring inbox traffic."""
    del args, kwargs
    client = _client()
    return json.dumps({"peer": client.peer_id, "reclaimed": client.reclaim_stale()})


def _claimed_or_processing(client: Client, msg_id: str, lease_token: str | None = None) -> ClaimedMessage:
    claimed = None if lease_token else client.claim(msg_id=msg_id)
    if claimed is not None:
        return claimed
    path = client.paths.processing / f"{msg_id}.json"
    if not path.is_file():
        raise RuntimeError(f"message {msg_id} is not claimed")
    env = envelope_from_dict(read_json(path), max_payload_bytes=client.config.max_payload_bytes)
    return ClaimedMessage(envelope=env, peer_id=client.peer_id, path=path, worker_id=client.worker_id, lease_token=lease_token)


def bot_coms_ack(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    client = _client()
    result = a.get("result")
    if isinstance(result, str):
        result = json.loads(result)
    client.ack(_claimed_or_processing(client, a["id"], a.get("lease_token")), result=result if isinstance(result, dict) else None)
    return json.dumps({"acked": a["id"]})


def bot_coms_nack(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    client = _client()
    client.nack(
        _claimed_or_processing(client, a["id"], a.get("lease_token")),
        error=str(a.get("error") or "nack"),
        retryable=not bool(a.get("no_retry")),
    )
    return json.dumps({"nacked": a["id"]})


def bot_coms_status(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    client = _client()
    msg_id = a.get("id")
    if msg_id:
        st = client.status(msg_id)
        out: dict[str, Any] = {
            "id": st.id,
            "location": st.location,
            "dead_letter_reason": st.dead_letter_reason,
        }
        if st.envelope:
            out["envelope"] = st.envelope.to_dict()
        return json.dumps(out)
    counts = {
        "inbox": len(list(client.paths.inbox.glob("*.json"))),
        "processing": len(list(client.paths.processing.glob("*.json"))),
        "acked": len(list(client.paths.acked.glob("*.json"))),
        "dead_letter": len(list(client.paths.dead_letter.glob("*.json"))),
    }
    return json.dumps({"peer": client.peer_id, "counts": counts})


def _parse_payload(raw: Any) -> dict[str, Any]:
    payload = raw
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else {}


def bot_coms_request(args: dict | None = None, **kwargs) -> str:
    """Synchronous request/wait path for agent callers."""
    a = _args(args, kwargs)
    client = _client()
    try:
        result = client.request(
            a["to"],
            _parse_payload(a.get("payload")),
            timeout_s=float(a.get("timeout_s", 120)),
            headers=_resolve_send_headers(a),
            idempotency_key=a.get("idempotency_key"),
        )
    except (CallTimeout, CallDeadLetter, PermissionDenied) as exc:
        error, reason = map_call_error(exc)
        return json.dumps({"ok": False, "error": error, "reason": reason})
    except Exception as exc:
        error, reason = map_call_error(exc)
        return json.dumps({"ok": False, "error": error, "reason": reason})
    return json.dumps(
        {
            "ok": True,
            "correlation_id": result.correlation_id,
            "payload": result.payload,
            "from": result.response_envelope.from_peer,
        }
    )


def bot_coms_emit(args: dict | None = None, **kwargs) -> str:
    """Fire-and-forget send for agent callers."""
    a = _args(args, kwargs)
    client = _client()
    try:
        env = client.fire(
            a["to"],
            _parse_payload(a.get("payload")),
            msg_type=a.get("type", "event"),
            headers=_resolve_send_headers(a),
            idempotency_key=a.get("idempotency_key"),
        )
    except PermissionDenied as exc:
        error, reason = map_call_error(exc)
        return json.dumps({"ok": False, "error": error, "reason": reason})
    except Exception as exc:
        error, reason = map_call_error(exc)
        return json.dumps({"ok": False, "error": error, "reason": reason})
    return json.dumps({"ok": True, "envelope": env.to_dict()})


SEND_SCHEMA = {
    "name": "bot_coms_send",
    "description": "Enqueue a bot-coms message into a peer inbox (filesystem spool).",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Destination peer id"},
            "type": {"type": "string", "description": "request | response | event"},
            "payload": {"type": "object", "description": "JSON object payload"},
            "idempotency_key": {"type": "string"},
            "correlation_id": {"type": "string"},
            "reply_to": {"type": "string"},
            "ttl_s": {"type": "number"},
            "headers": {
                "type": "object",
                "description": "Opaque string map; use source for human conversation routing",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["to", "payload"],
    },
}

CLAIM_SCHEMA = {
    "name": "bot_coms_claim",
    "description": "Claim the next eligible inbox message for this peer.",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Optional specific message id"}},
    },
}

RECLAIM_SCHEMA = {
    "name": "bot_coms_reclaim",
    "description": "Return this peer's stale processing messages to its inbox.",
    "parameters": {"type": "object", "properties": {}},
}

ACK_SCHEMA = {
    "name": "bot_coms_ack",
    "description": "Acknowledge a claimed message, optionally with a result payload.",
    "parameters": {
        "type": "object",
        "properties": {
            "lease_token": {"type":"string", "description":"Claim token returned by team_inbox or bot_coms_claim"},
            "id": {"type": "string"},
            "result": {"type": "object"},
        },
        "required": ["id"],
    },
}

NACK_SCHEMA = {
    "name": "bot_coms_nack",
    "description": "Negative-ack a claimed message (retry or poison).",
    "parameters": {
        "type": "object",
        "properties": {
            "lease_token": {"type":"string", "description":"Claim token returned by team_inbox or bot_coms_claim"},
            "id": {"type": "string"},
            "error": {"type": "string"},
            "no_retry": {"type": "boolean"},
        },
        "required": ["id"],
    },
}

STATUS_SCHEMA = {
    "name": "bot_coms_status",
    "description": "Status for one message id, or spool counts for this peer.",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
    },
}

REQUEST_SCHEMA = {
    "name": "bot_coms_request",
    "description": "Send a request, wait for the correlated result, and auto-ack the terminal response.",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Destination peer id"},
            "payload": {"type": "object", "description": "JSON object payload"},
            "timeout_s": {"type": "number", "default": 120},
            "headers": {
                "type": "object",
                "description": "Opaque string map; use source for human conversation routing",
                "additionalProperties": {"type": "string"},
            },
            "idempotency_key": {"type": "string"},
        },
        "required": ["to", "payload"],
    },
}

EMIT_SCHEMA = {
    "name": "bot_coms_emit",
    "description": "Fire-and-forget enqueue (event or request without waiting).",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Destination peer id"},
            "payload": {"type": "object", "description": "JSON object payload"},
            "type": {"type": "string", "description": "event | request", "default": "event"},
            "headers": {
                "type": "object",
                "description": "Opaque string map",
                "additionalProperties": {"type": "string"},
            },
            "idempotency_key": {"type": "string"},
        },
        "required": ["to", "payload"],
    },
}
