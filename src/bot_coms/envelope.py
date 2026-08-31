"""Envelope parse / validate / serialize and ULID generation."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from bot_coms.types import (
    MESSAGE_TYPES,
    PEER_ID_PATTERN,
    SCHEMA_VERSION,
    ULID_ALPHABET,
    Clock,
    Envelope,
    ValidationError,
)

_PEER_RE = re.compile(PEER_ID_PATTERN)
_ULID_RE = re.compile(rf"^[{ULID_ALPHABET}]{{26}}$")
_PATHY = re.compile(r"[\\/\x00]|\\.\\.")


def generate_ulid(now: datetime | None = None) -> str:
    if now is None:
        ms = int(time.time() * 1000)
    else:
        ms = int(now.timestamp() * 1000)
    ms &= (1 << 48) - 1
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode_crockford(ms, 10) + _encode_crockford(rand, 16)


def _encode_crockford(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(ULID_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def format_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def parse_ts(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValidationError("timestamp must be a non-empty string")
    raw = value
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid timestamp: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def schema_major(version: str) -> int:
    try:
        return int(str(version).split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"invalid schema_version: {version}") from exc


def _reject_path_chars(name: str, field: str) -> None:
    if _PATHY.search(name) or name in {".", ".."}:
        raise ValidationError(f"{field} contains path characters")


def validate_peer_id(peer_id: str, *, field: str = "peer") -> str:
    if not isinstance(peer_id, str) or not peer_id:
        raise ValidationError(f"{field} must be a non-empty string")
    _reject_path_chars(peer_id, field)
    if not _PEER_RE.match(peer_id):
        raise ValidationError(f"invalid {field}: {peer_id}")
    return peer_id


def validate_ulid(value: str, *, field: str = "id") -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    _reject_path_chars(value, field)
    if not _ULID_RE.match(value.upper()):
        raise ValidationError(f"invalid {field} ULID")
    return value.upper()


def payload_size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def envelope_from_dict(data: Any, *, max_payload_bytes: int = 1 << 20) -> Envelope:
    if not isinstance(data, dict):
        raise ValidationError("envelope must be a JSON object")
    version = data.get("schema_version")
    if not isinstance(version, str):
        raise ValidationError("schema_version is required")
    if schema_major(version) != 1:
        raise ValidationError(f"unsupported schema_version major: {version}")

    required = (
        "id",
        "idempotency_key",
        "correlation_id",
        "from",
        "to",
        "type",
        "payload",
        "created_at",
        "expires_at",
        "attempt",
    )
    missing = [k for k in required if k not in data]
    if missing:
        raise ValidationError(f"missing fields: {', '.join(missing)}")

    msg_id = validate_ulid(data["id"])
    idem = data["idempotency_key"]
    corr = data["correlation_id"]
    if not isinstance(idem, str) or not idem:
        raise ValidationError("idempotency_key must be a non-empty string")
    if not isinstance(corr, str) or not corr:
        raise ValidationError("correlation_id must be a non-empty string")
    _reject_path_chars(idem, "idempotency_key")
    _reject_path_chars(corr, "correlation_id")

    from_peer = validate_peer_id(data["from"], field="from")
    to_peer = validate_peer_id(data["to"], field="to")
    msg_type = data["type"]
    if msg_type not in MESSAGE_TYPES:
        raise ValidationError(f"invalid type: {msg_type}")
    payload = data["payload"]
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")
    if payload_size(payload) > max_payload_bytes:
        raise ValidationError("payload exceeds max_payload_bytes")

    created = data["created_at"]
    expires = data["expires_at"]
    created_dt = parse_ts(created)
    expires_dt = parse_ts(expires)
    if not (expires_dt > created_dt):
        raise ValidationError("expires_at must be greater than created_at")

    attempt = data["attempt"]
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise ValidationError("attempt must be an integer >= 0")

    reply_to = data.get("reply_to")
    if reply_to is not None:
        reply_to = validate_peer_id(reply_to, field="reply_to")

    next_visible = data.get("next_visible_at")
    if next_visible is not None:
        parse_ts(next_visible)

    priority = data.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise ValidationError("priority must be an integer")

    headers = data.get("headers")
    if headers is not None:
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise ValidationError("headers must be a string map")
        if payload_size(headers) > 4096 or len(headers) > 32:
            raise ValidationError("headers exceed size cap")

    receipt = data.get("receipt")
    if receipt is not None and not isinstance(receipt, dict):
        raise ValidationError("receipt must be an object")

    return Envelope(
        schema_version=version,
        id=msg_id,
        idempotency_key=idem,
        correlation_id=corr,
        from_peer=from_peer,
        to=to_peer,
        type=msg_type,
        payload=payload,
        created_at=created,
        expires_at=expires,
        attempt=attempt,
        reply_to=reply_to,
        next_visible_at=next_visible,
        priority=priority,
        headers=headers,
        receipt=receipt,
    )


def dumps_envelope(env: Envelope) -> str:
    return json.dumps(env.to_dict(), ensure_ascii=False, separators=(",", ":"))


def new_envelope(
    *,
    from_peer: str,
    to: str,
    msg_type: str,
    payload: dict[str, Any],
    clock: Clock,
    ttl_s: float,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    reply_to: str | None = None,
    priority: int = 0,
    headers: dict[str, str] | None = None,
    max_payload_bytes: int = 1 << 20,
) -> Envelope:
    now = clock.now()
    msg_id = generate_ulid(now)
    created = format_ts(now)
    expires = format_ts(now + timedelta(seconds=ttl_s))
    env = Envelope(
        schema_version=SCHEMA_VERSION,
        id=msg_id,
        idempotency_key=idempotency_key or msg_id,
        correlation_id=correlation_id or msg_id,
        from_peer=from_peer,
        to=to,
        type=msg_type,
        payload=payload,
        created_at=created,
        expires_at=expires,
        attempt=0,
        reply_to=reply_to,
        priority=priority,
        headers=headers,
    )
    return envelope_from_dict(env.to_dict(), max_payload_bytes=max_payload_bytes)
