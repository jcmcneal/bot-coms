"""Hermes tool handlers → bot_coms.Client (env-configured)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from bot_coms import Client
from bot_coms.atomic import read_json
from bot_coms.envelope import envelope_from_dict
from bot_coms.types import ClaimedMessage


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
    )
    return json.dumps(env.to_dict())


def bot_coms_claim(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    claimed = _client().claim(msg_id=a.get("id"))
    if claimed is None:
        return json.dumps({"claimed": False})
    return json.dumps({"claimed": True, "envelope": claimed.envelope.to_dict()})


def _claimed_or_processing(client: Client, msg_id: str) -> ClaimedMessage:
    claimed = client.claim(msg_id=msg_id)
    if claimed is not None:
        return claimed
    path = client.paths.processing / f"{msg_id}.json"
    if not path.is_file():
        raise RuntimeError(f"message {msg_id} is not claimed")
    env = envelope_from_dict(read_json(path), max_payload_bytes=client.config.max_payload_bytes)
    return ClaimedMessage(envelope=env, peer_id=client.peer_id, path=path, worker_id=client.worker_id)


def bot_coms_ack(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    client = _client()
    result = a.get("result")
    if isinstance(result, str):
        result = json.loads(result)
    client.ack(_claimed_or_processing(client, a["id"]), result=result if isinstance(result, dict) else None)
    return json.dumps({"acked": a["id"]})


def bot_coms_nack(args: dict | None = None, **kwargs) -> str:
    a = _args(args, kwargs)
    client = _client()
    client.nack(
        _claimed_or_processing(client, a["id"]),
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

ACK_SCHEMA = {
    "name": "bot_coms_ack",
    "description": "Acknowledge a claimed message, optionally with a result payload.",
    "parameters": {
        "type": "object",
        "properties": {
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
