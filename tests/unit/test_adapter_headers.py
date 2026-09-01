from __future__ import annotations

import json
from unittest.mock import patch

from hermes_bot_coms import tools as adapter_tools


def test_bot_coms_send_passes_explicit_headers(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["a", "b"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    out = adapter_tools.bot_coms_send(
        to="b",
        payload={"ping": 1},
        headers={"source": "discord:99"},
        reply_to="a",
    )
    env = json.loads(out)
    assert env["headers"] == {"source": "discord:99"}


def test_bot_coms_send_auto_stamps_session_source(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["pm", "swe"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")

    with patch.object(adapter_tools, "_session_source", return_value="discord:1543040481368346765"):
        out = adapter_tools.bot_coms_send(to="swe", payload={"text": "PING"}, reply_to="pm")
    env = json.loads(out)
    assert env["headers"] == {"source": "discord:1543040481368346765"}


def test_bot_coms_send_explicit_source_wins_over_session(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["pm", "swe"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")

    with patch.object(adapter_tools, "_session_source", return_value="discord:auto"):
        out = adapter_tools.bot_coms_send(
            to="swe",
            payload={"text": "PING"},
            headers={"source": "telegram:manual"},
            reply_to="pm",
        )
    env = json.loads(out)
    assert env["headers"] == {"source": "telegram:manual"}
