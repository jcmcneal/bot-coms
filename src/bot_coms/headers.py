"""Opaque routing headers (platform-blind)."""

from __future__ import annotations

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
