"""Validate lean bot-coms slice payloads (``{intent, slice}`` doorbell)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

INTENTS = frozenset({"assign", "report_only", "report", "cancel"})
RECEIPT_INTENTS = frozenset({"ack", "fail"})

_SLICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_LEGACY_PING_RE = re.compile(
    r"^\s*PING\s+read\s+.*BUS\.md\s+item\s+(\S+)",
    re.IGNORECASE,
)

# Worker-owned completion notes live below this heading; digest excludes them.
_NOTES_HEADING_RE = re.compile(r"^## Notes\s*$", re.MULTILINE)


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
    if schema_version != SCHEMA_VERSION:
        raise PayloadError("unsupported schema_version", code="INVALID_SCHEMA")

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


def assignment_spec_text(text: str) -> str:
    """Return the PM-owned assignment body above the worker ``## Notes`` section."""
    match = _NOTES_HEADING_RE.search(text)
    if match is None:
        return text
    return text[: match.start()].rstrip("\n")


def sha256_whole_file(path: str | Path) -> str:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_assignment_spec(path: str | Path) -> str:
    """Digest of assignment prose excluding the worker-owned ``## Notes`` tail."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"assignment file not found: {p}")
    spec = assignment_spec_text(p.read_text(encoding="utf-8"))
    return hashlib.sha256(spec.encode("utf-8")).hexdigest()


def verify_content_digest(path: str, expected: str | None) -> None:
    if not expected:
        return
    p = Path(path)
    if not p.is_file():
        raise PayloadError(f"assignment file not found: {path}", code="MISSING_ASSIGNMENT")
    if sha256_assignment_spec(path) == expected:
        return
    # Legacy rows registered before the ## Notes carve-out used whole-file digests.
    if sha256_whole_file(path) == expected:
        return
    raise PayloadError(
        "assignment content hash mismatch (stale_assignment)",
        code="STALE_ASSIGNMENT",
    )
