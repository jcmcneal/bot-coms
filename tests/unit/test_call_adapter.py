from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from bot_coms import PermissionDenied, Worker
from hermes_bot_coms import tools as adapter_tools


def test_bot_coms_request_success(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["a", "b"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    from bot_coms import Client

    b = Client(root, "b")
    worker = threading.Thread(
        target=lambda: Worker(
            b, lambda _c: {"ok": True}, poll_interval_s=0.01
        ).run_until_idle(idle_rounds=10),
        daemon=True,
    )
    worker.start()

    out = json.loads(adapter_tools.bot_coms_request(to="b", payload={"ping": 1}, timeout_s=5))
    worker.join(timeout=2)
    assert out["ok"] is True
    assert out["payload"] == {"ok": True}
    assert out["from"] == "b"
    assert "correlation_id" in out


def test_bot_coms_request_timeout(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["a", "b"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    out = json.loads(adapter_tools.bot_coms_request(to="b", payload={"ping": 1}, timeout_s=0.05))
    assert out["ok"] is False
    assert out["reason"] == "timeout"
    assert "error" in out
    assert "payload" not in out["error"]


def test_bot_coms_emit_fire_and_forget(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["a", "b"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    out = json.loads(adapter_tools.bot_coms_emit(to="b", payload={"event": 1}))
    assert out["ok"] is True
    assert out["envelope"]["type"] == "event"
    assert "reply_to" not in out["envelope"]


def test_bot_coms_request_permission_error(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["a", "b"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    with patch.object(adapter_tools, "_client") as mock_client:
        client = mock_client.return_value
        client.request.side_effect = PermissionDenied("denied")
        out = json.loads(adapter_tools.bot_coms_request(to="b", payload={"x": 1}))
    assert out["ok"] is False
    assert out["reason"] == "permission"


def test_atomic_tools_remain_compatible(tmp_path, monkeypatch) -> None:
    from bot_coms.spool import init_spool

    root = tmp_path / "spool"
    init_spool(root, ["a", "b"])
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(root))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    sent = json.loads(adapter_tools.bot_coms_send(to="b", payload={"legacy": 1}, type="event"))
    assert sent["to"] == "b"

    monkeypatch.setenv("BOT_COMS_PEER_ID", "b")
    claimed = json.loads(adapter_tools.bot_coms_claim())
    assert claimed["claimed"] is True
    acked = json.loads(adapter_tools.bot_coms_ack(id=claimed["envelope"]["id"]))
    assert acked["acked"] == claimed["envelope"]["id"]
