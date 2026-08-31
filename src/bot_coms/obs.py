"""Structured JSON logs to stderr and redacted peer audit.jsonl."""

from __future__ import annotations

import json
import sys
from typing import Any

from bot_coms.atomic import append_jsonl_line
from bot_coms.envelope import format_ts
from bot_coms.paths import PeerPaths
from bot_coms.types import Clock

_REDACT_KEYS = frozenset({"token", "authorization", "password", "secret", "payload"})


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if str(key).lower() in _REDACT_KEYS:
                out[key] = "[redacted]"
            else:
                out[key] = _scrub(value)
        return out
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def log_event(level: str, event: str, **fields: Any) -> None:
    rec = {"level": level, "event": event, **_scrub(fields)}
    sys.stderr.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def audit(paths: PeerPaths, clock: Clock, event: str, *, file_mode: int = 0o600, **fields: Any) -> None:
    rec = {
        "ts": format_ts(clock.now()),
        "peer": paths.peer_id,
        "event": event,
        **_scrub(fields),
    }
    append_jsonl_line(paths.audit, rec, file_mode=file_mode)
    log_event("info", event, peer=paths.peer_id, **fields)
