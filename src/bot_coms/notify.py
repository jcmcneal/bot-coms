"""Outbound notify via configured argv (platform-blind)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from bot_coms.envelope import dumps_envelope
from bot_coms.headers import default_source, is_notifiable_source, source_from_headers
from bot_coms.profile_env import resolve_completion_sink_argv_raw, resolve_notify_argv_raw
from bot_coms.types import ClaimedMessage, Envelope, HandlerError, SkipMessage


def notify_argv_from_env() -> list[str]:
    raw = resolve_notify_argv_raw()
    if not raw:
        raise RuntimeError("BOT_COMS_NOTIFY_ARGV is required for source_argv handler")
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise RuntimeError("BOT_COMS_NOTIFY_ARGV must be a non-empty JSON array")
    return [str(part) for part in data]


def completion_sink_argv_from_env(*, peer_id: str | None = None) -> list[str]:
    raw = resolve_completion_sink_argv_raw(peer_id=peer_id)
    if not raw:
        raise RuntimeError(
            "BOT_COMS_COMPLETION_SINK_ARGV is required for completion_sink_argv handler"
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("BOT_COMS_COMPLETION_SINK_ARGV must be valid JSON") from exc
    if (
        not isinstance(data, list)
        or not data
        or not all(isinstance(part, str) for part in data)
        or not data[0]
    ):
        raise RuntimeError(
            "BOT_COMS_COMPLETION_SINK_ARGV must be a non-empty JSON array of strings"
        )
    return data


def format_notify_argv(argv: list[str], source: str) -> list[str]:
    return [part.format(source=source) for part in argv]


def format_completion_sink_argv(
    argv: list[str], *, route: str, source: str
) -> list[str]:
    """Substitute routing tokens within argv elements, never through a shell."""
    return [
        part.replace("{route}", route).replace("{source}", source)
        for part in argv
    ]


def payload_text(payload: dict[str, Any]) -> str:
    line = summary_line(payload)
    if line:
        return line
    return json.dumps(payload, ensure_ascii=False, indent=2)


def summary_line(payload: dict[str, Any]) -> str | None:
    """One-line human summary for terminal report acks."""
    if payload.get("intent") != "ack":
        return None
    slice_id = payload.get("slice")
    verdict = payload.get("verdict")
    if not isinstance(slice_id, str) or not slice_id:
        return None
    if not isinstance(verdict, str) or not verdict.strip():
        return None
    parts = [slice_id, verdict.strip()]
    evidence = payload.get("evidence")
    if isinstance(evidence, str) and evidence.strip():
        parts.append(f"— {evidence.strip()}")
    return " ".join(parts)


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


def run_completion_sink_argv(
    argv: list[str], *, route: str, source: str, envelope: Envelope
) -> None:
    try:
        proc = subprocess.run(  # noqa: S603 — argv from operator config
            format_completion_sink_argv(argv, route=route, source=source),
            input=dumps_envelope(envelope) + "\n",
            text=True,
            capture_output=True,
            check=False,
            shell=False,
        )
    except OSError as exc:
        raise HandlerError(
            f"completion sink argv could not start: {exc}",
            retryable=True,
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:2000]
        suffix = f": {detail}" if detail else ""
        raise HandlerError(
            f"completion sink argv failed (exit {proc.returncode}){suffix}",
            retryable=True,
        )


def source_argv(claimed: ClaimedMessage) -> dict[str, Any] | None:
    """Worker handler: deliver terminal responses with headers.source via argv."""
    env = claimed.envelope
    if (env.headers or {}).get("delivery") == "internal":
        return None
    if env.type != "response":
        raise SkipMessage()
    source = source_from_headers(env.headers)
    if not is_notifiable_source(source):
        source = default_source()
    if not is_notifiable_source(source):
        return None
    run_notify_argv(notify_argv_from_env(), source, env.payload)
    return None


def completion_sink_argv(claimed: ClaimedMessage) -> dict[str, Any] | None:
    """Deliver an external response envelope to a structured argv callback."""
    env = claimed.envelope
    if env.type != "response" or (env.headers or {}).get("delivery") == "internal":
        raise SkipMessage()
    run_completion_sink_argv(
        completion_sink_argv_from_env(peer_id=claimed.peer_id),
        route=env.to,
        source=source_from_headers(env.headers),
        envelope=env,
    )
    return None
