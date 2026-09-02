"""Opaque routing headers (platform-blind)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from bot_coms.profile_env import resolve_default_source
from bot_coms.types import Envelope

SOURCE_HEADER = "source"

# Platforms that are not human messaging surfaces; never auto-stamp or notify.
_NON_MESSAGING_PLATFORMS = frozenset({"", "cli", "tui", "local"})


def forward_headers(envelope: Envelope | None) -> dict[str, str] | None:
    """Copy inbound headers for re-assign; returns None when absent."""
    if envelope is None or not envelope.headers:
        return None
    return dict(envelope.headers)


def source_from_headers(headers: dict[str, str] | None) -> str:
    if not headers:
        return ""
    return (headers.get(SOURCE_HEADER) or "").strip()


def source_platform(source: str) -> str:
    """Return the platform prefix before the first colon, or empty."""
    if not source:
        return ""
    return source.split(":", 1)[0].strip().lower()


def is_notifiable_source(source: str) -> bool:
    """True when source is non-empty and not a local-only platform."""
    platform = source_platform(source)
    return bool(platform) and platform not in _NON_MESSAGING_PLATFORMS


def format_source(platform: str, chat_id: str, thread_id: str = "") -> str:
    """Build opaque ``headers.source`` in ``hermes send --to`` shape."""
    platform = (platform or "").strip()
    chat_id = (chat_id or "").strip()
    thread_id = (thread_id or "").strip()
    if not platform or not chat_id:
        return ""
    if thread_id:
        return f"{platform}:{chat_id}:{thread_id}"
    return f"{platform}:{chat_id}"

_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")


def discord_snowflake(token: str) -> str:
    """Return token if it looks like a Discord snowflake, else empty."""
    token = (token or "").strip()
    return token if _SNOWFLAKE_RE.fullmatch(token) else ""


def snowflake_from_session_key(session_key: str) -> str:
    """Pull a Discord snowflake out of a gateway session key."""
    parts = (session_key or "").split(":")
    try:
        idx = parts.index("discord")
    except ValueError:
        return ""
    for part in parts[idx + 1 :]:
        sf = discord_snowflake(part)
        if sf:
            return sf
    return ""


def normalize_source(source: str, *, session_key: str = "") -> str:
    """Return a ``hermes send --to`` target. Discord channel names are not sendable."""
    source = (source or "").strip()
    platform = source_platform(source)
    if platform != "discord":
        return source
    rest = source.split(":", 1)[1] if ":" in source else ""
    for tok in rest.replace("/", ":").split(":"):
        sf = discord_snowflake(tok.strip())
        if sf:
            return format_source("discord", sf)
    sf = snowflake_from_session_key(session_key)
    if sf:
        return format_source("discord", sf)
    return ""


def session_source_from_env(getenv) -> str:
    """Build source from Hermes session env. Prefer snowflake over chat name."""
    platform = (getenv("HERMES_SESSION_PLATFORM", "") or "").strip().lower()
    if platform in {"", "cli", "tui", "local"}:
        return ""
    chat_id = (getenv("HERMES_SESSION_CHAT_ID", "") or "").strip()
    thread_id = (getenv("HERMES_SESSION_THREAD_ID", "") or "").strip()
    key = (getenv("HERMES_SESSION_KEY", "") or "").strip()
    if platform == "discord":
        return normalize_source(format_source(platform, chat_id, thread_id), session_key=key)
    return format_source(platform, chat_id, thread_id)



def default_source(*, peer_id: str | None = None) -> str:
    """Fold/notify target from env, then profile or team dotenv files."""
    raw = resolve_default_source(peer_id=peer_id)
    return raw if is_notifiable_source(raw) else ""


def default_source_from_env() -> str:
    """Fold target for cli/tui/local assigns (``BOT_COMS_DEFAULT_SOURCE``)."""
    return default_source()


def resolve_assign_headers(
    headers: dict[str, str] | None,
    *,
    session_source: str = "",
    peer_id: str | None = None,
) -> dict[str, str] | None:
    """Stamp ``headers.source`` from explicit, session, or default env/file."""
    out: dict[str, str] = dict(headers) if headers else {}
    source = source_from_headers(out)
    if not source:
        source = (session_source or "").strip() or default_source(peer_id=peer_id)
    if source_platform(source) == "discord":
        source = normalize_source(source) or default_source(peer_id=peer_id)
    if not source:
        return headers
    out[SOURCE_HEADER] = source
    return out


def stamp_response_default_source(path: Path, *, peer_id: str | None = None) -> bool:
    """Stamp ``headers.source`` on a headerless ``type=response`` inbox envelope."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or data.get("type") != "response":
        return False
    headers = data.get("headers")
    if not isinstance(headers, dict):
        headers = {}
    if is_notifiable_source(source_from_headers(headers)):
        return False
    source = default_source(peer_id=peer_id)
    if not is_notifiable_source(source):
        return False
    headers = dict(headers)
    headers[SOURCE_HEADER] = source
    data["headers"] = headers
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args[:1] == ["stamp-response"] and len(args) == 3:
        peer_id = args[2] if args[2] else None
        stamped = stamp_response_default_source(Path(args[1]), peer_id=peer_id)
        return 0 if stamped else 1
    if args[:1] == ["print-default"]:
        peer_id = args[1] if len(args) > 1 else None
        sys.stdout.write(default_source(peer_id=peer_id) + "\n")
        return 0
    sys.stderr.write("usage: python -m bot_coms.headers stamp-response <mail.json> [peer_id]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
