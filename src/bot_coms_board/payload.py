"""Validate lean bot-coms slice payloads (``{intent, slice}`` doorbell)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1.0"

INTENTS = frozenset({"assign", "report_only", "report", "cancel"})

_SLICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_LEGACY_PING_RE = re.compile(
    r"^\s*PING\s+read\s+.*BUS\.md\s+item\s+(\S+)",
    re.IGNORECASE,
)


@dataclass
class SlicePayload:
    schema_version: str
    intent: str
    slice: str

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "intent": self.intent,
            "slice": self.slice,
        }

    def idempotency_key(self, content_sha256: str | None) -> str:
        prefix = (content_sha256 or "none")[:8]
        return f"{self.intent}:{self.slice}:{prefix}"


class PayloadError(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_PAYLOAD") -> None:
        super().__init__(message)
        self.code = code


def parse_payload(raw: dict[str, Any] | None) -> SlicePayload:
    if not isinstance(raw, dict):
        raise PayloadError("payload must be a JSON object")

    text = raw.get("text")
    if isinstance(text, str) and text.strip():
        return _parse_legacy_text(text.strip())

    intent = raw.get("intent")
    slice_id = raw.get("slice")
    schema_version = raw.get("schema_version", SCHEMA_VERSION)

    if not isinstance(intent, str) or intent not in INTENTS:
        raise PayloadError(
            f"intent must be one of {sorted(INTENTS)}",
            code="INVALID_INTENT",
        )
    if not isinstance(slice_id, str) or not _SLICE_RE.fullmatch(slice_id):
        raise PayloadError(
            "slice must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
            code="INVALID_SLICE",
        )
    if not isinstance(schema_version, str):
        raise PayloadError("schema_version must be a string")

    extra_keys = set(raw.keys()) - {"schema_version", "intent", "slice"}
    if extra_keys:
        raise PayloadError(
            f"unexpected payload keys: {sorted(extra_keys)}; use SQL register for metadata",
            code="EXTRA_KEYS",
        )

    return SlicePayload(
        schema_version=schema_version,
        intent=intent,
        slice=slice_id,
    )


def _parse_legacy_text(text: str) -> SlicePayload:
    raise PayloadError(
        "legacy text PING payloads are rejected; use structured {intent, slice}",
        code="LEGACY_PING",
    )


def try_parse_legacy_bus_ping(text: str) -> str | None:
    """Return slice id from legacy ping text (migration tooling only)."""
    m = _LEGACY_PING_RE.match(text.strip())
    return m.group(1) if m else None


def verify_content_digest(path: str, expected: str | None) -> None:
    import hashlib
    from pathlib import Path

    if not expected:
        return
    p = Path(path)
    if not p.is_file():
        raise PayloadError(f"assignment file not found: {path}", code="MISSING_ASSIGNMENT")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    if digest != expected:
        raise PayloadError(
            "assignment content hash mismatch (stale_assignment)",
            code="STALE_ASSIGNMENT",
        )
