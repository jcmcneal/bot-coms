from __future__ import annotations

from bot_coms.headers import (
    SOURCE_HEADER,
    format_source,
    forward_headers,
    is_notifiable_source,
    source_from_headers,
    source_platform,
)
from bot_coms.types import Envelope


def _env(headers: dict[str, str] | None = None) -> Envelope:
    return Envelope(
        schema_version="1.0",
        id="01HZZZZZZZZZZZZZZZZZZZZZZZ",
        idempotency_key="k",
        correlation_id="c",
        from_peer="a",
        to="b",
        type="request",
        payload={},
        created_at="2026-08-31T00:00:00.000Z",
        expires_at="2026-09-01T00:00:00.000Z",
        attempt=0,
        headers=headers,
    )


def test_forward_headers_copies_map() -> None:
    env = _env({"source": "discord:1"})
    assert forward_headers(env) == {"source": "discord:1"}


def test_forward_headers_none_when_absent() -> None:
    assert forward_headers(_env()) is None
    assert forward_headers(None) is None


def test_format_source_with_thread() -> None:
    assert format_source("discord", "1543040481368346765", "99") == "discord:1543040481368346765:99"


def test_format_source_without_thread() -> None:
    assert format_source("telegram", "-100123") == "telegram:-100123"


def test_is_notifiable_source() -> None:
    assert is_notifiable_source("discord:1")
    assert not is_notifiable_source("")
    assert not is_notifiable_source("cli:foo")
    assert not is_notifiable_source("tui:foo")
    assert not is_notifiable_source("local:foo")


def test_source_from_headers() -> None:
    assert source_from_headers({SOURCE_HEADER: "discord:x"}) == "discord:x"
    assert source_from_headers({}) == ""


def test_source_platform() -> None:
    assert source_platform("discord:1:2") == "discord"
    assert source_platform("") == ""
