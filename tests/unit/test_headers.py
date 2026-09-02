from __future__ import annotations

import json
from pathlib import Path

from bot_coms.headers import (
    SOURCE_HEADER,
    default_source,
    default_source_from_env,
    format_source,
    forward_headers,
    is_notifiable_source,
    resolve_assign_headers,
    source_from_headers,
    source_platform,
    stamp_response_default_source,
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


def test_default_source_from_env(monkeypatch) -> None:
    monkeypatch.delenv("BOT_COMS_DEFAULT_SOURCE", raising=False)
    assert default_source_from_env() == ""
    monkeypatch.setenv("BOT_COMS_DEFAULT_SOURCE", "discord:1543040481368346765")
    assert default_source_from_env() == "discord:1543040481368346765"
    monkeypatch.setenv("BOT_COMS_DEFAULT_SOURCE", "cli:local")
    assert default_source_from_env() == ""


def test_resolve_assign_headers_prefers_explicit() -> None:
    out = resolve_assign_headers(
        {"source": "telegram:1"},
        session_source="discord:2",
    )
    assert out == {"source": "telegram:1"}


def test_resolve_assign_headers_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_COMS_DEFAULT_SOURCE", "discord:99")
    out = resolve_assign_headers(None, session_source="")
    assert out == {"source": "discord:99"}


def _write_profile_default_source(
    tmp_path: Path,
    monkeypatch,
    *,
    source: str = "discord:file-channel",
) -> None:
    team_root = tmp_path / "team"
    team_root.mkdir()
    peers_yaml = team_root / "peers.yaml"
    peers_yaml.write_text(
        "peers:\n  - id: pm\n    profile: project-manager\n",
        encoding="utf-8",
    )
    profile_env = tmp_path / ".hermes" / "profiles" / "project-manager" / ".env"
    profile_env.parent.mkdir(parents=True)
    profile_env.write_text(f"BOT_COMS_DEFAULT_SOURCE={source}\n", encoding="utf-8")
    monkeypatch.delenv("BOT_COMS_DEFAULT_SOURCE", raising=False)
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_PEERS_YAML", str(peers_yaml))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")


def test_default_source_from_profile_env_file(tmp_path, monkeypatch) -> None:
    _write_profile_default_source(tmp_path, monkeypatch)
    assert default_source(peer_id="pm") == "discord:file-channel"
    assert default_source_from_env() == "discord:file-channel"


def test_resolve_assign_headers_falls_back_to_profile_env_file(tmp_path, monkeypatch) -> None:
    _write_profile_default_source(tmp_path, monkeypatch)
    out = resolve_assign_headers(None, session_source="", peer_id="pm")
    assert out == {"source": "discord:file-channel"}


def test_stamp_response_default_source_heals_headerless_response(tmp_path, monkeypatch) -> None:
    _write_profile_default_source(tmp_path, monkeypatch, source="discord:healed")
    mail = tmp_path / "pm-response.json"
    mail.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "01HZZZZZZZZZZZZZZZZZZZZZZZ",
                "type": "response",
                "headers": None,
                "payload": {"ok": True},
            }
        ),
        encoding="utf-8",
    )
    assert stamp_response_default_source(mail, peer_id="pm") is True
    data = json.loads(mail.read_text(encoding="utf-8"))
    assert data["headers"] == {"source": "discord:healed"}
    assert stamp_response_default_source(mail, peer_id="pm") is False


def test_normalize_discord_channel_name_needs_session_key() -> None:
    from bot_coms.headers import normalize_source

    assert normalize_source("discord:Mora View/#grok-team") == ""
    key = "agent:project-manager:discord:group:1543040481368346765"
    assert (
        normalize_source("discord:Mora View/#grok-team", session_key=key)
        == "discord:1543040481368346765"
    )


def test_normalize_discord_already_snowflake() -> None:
    from bot_coms.headers import normalize_source

    assert normalize_source("discord:1543040481368346765") == "discord:1543040481368346765"


def test_session_source_from_env_prefers_snowflake_over_name() -> None:
    from bot_coms.headers import session_source_from_env

    env = {
        "HERMES_SESSION_PLATFORM": "discord",
        "HERMES_SESSION_CHAT_ID": "Mora View/#grok-team",
        "HERMES_SESSION_KEY": "agent:project-manager:discord:group:1543040481368346765",
    }
    assert session_source_from_env(lambda k, d="": env.get(k, d)) == "discord:1543040481368346765"


def test_resolve_assign_headers_rewrites_discord_name(monkeypatch) -> None:
    monkeypatch.setenv("BOT_COMS_DEFAULT_SOURCE", "discord:1543040481368346765")
    out = resolve_assign_headers({"source": "discord:Mora View/#grok-team"})
    assert out == {"source": "discord:1543040481368346765"}
