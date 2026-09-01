"""Outbound notify via configured argv (platform-blind)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from bot_coms.headers import is_notifiable_source, source_from_headers
from bot_coms.types import ClaimedMessage, HandlerError, SkipMessage


def notify_argv_from_env() -> list[str]:
    raw = os.environ.get("BOT_COMS_NOTIFY_ARGV", "")
    if not raw:
        raise RuntimeError("BOT_COMS_NOTIFY_ARGV is required for source_argv handler")
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise RuntimeError("BOT_COMS_NOTIFY_ARGV must be a non-empty JSON array")
    return [str(part) for part in data]


def format_notify_argv(argv: list[str], source: str) -> list[str]:
    return [part.format(source=source) for part in argv]


def payload_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def run_notify_argv(argv: list[str], source: str, payload: dict[str, Any]) -> None:
    proc = subprocess.run(  # noqa: S603 — argv from operator config
        format_notify_argv(argv, source),
        input=payload_text(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise HandlerError(
            f"notify argv failed (exit {proc.returncode}): {detail}",
            retryable=True,
        )


def source_argv(claimed: ClaimedMessage) -> dict[str, Any] | None:
    """Worker handler: deliver terminal responses with headers.source via argv."""
    env = claimed.envelope
    if env.type != "response":
        raise SkipMessage()
    source = source_from_headers(env.headers)
    if not is_notifiable_source(source):
        return None
    run_notify_argv(notify_argv_from_env(), source, env.payload)
    return None
