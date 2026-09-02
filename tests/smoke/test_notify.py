from __future__ import annotations

import json
import os
from pathlib import Path

from bot_coms import Client, Worker
from bot_coms.notify import source_argv, summary_line


def test_source_argv_runs_notify_and_acks(clients, tmp_path, monkeypatch) -> None:
    a, b = clients
    recorder = tmp_path / "notify.out"
    script = tmp_path / "record.sh"
    script.write_text(
        "#!/bin/sh\n"
        "echo \"$1\" >> \"$2\"\n"
        "cat >> \"$2\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv(
        "BOT_COMS_NOTIFY_ARGV",
        json.dumps([str(script), "{source}", str(recorder)]),
    )

    request = a.send(
        "b",
        "request",
        {"task": "x"},
        reply_to="a",
        headers={"source": "discord:channel-1"},
    )
    claimed = b.claim(msg_id=request.id)
    assert claimed is not None
    b.ack(claimed, result={"ok": True})

    response = a.claim()
    assert response is not None
    worker = Worker(a, source_argv, poll_interval_s=0.01)
    worker.handle_one(response)

    assert recorder.read_text(encoding="utf-8").startswith("discord:channel-1\n")
    assert "ok" in recorder.read_text(encoding="utf-8")
    assert not list(a.paths.inbox.glob("*.json"))
    assert list(a.paths.acked.glob(f"{response.envelope.id}.json"))


def test_source_argv_skips_non_response(clients) -> None:
    a, b = clients
    env = a.send("b", "request", {"x": 1}, reply_to="a")
    claimed = b.claim(msg_id=env.id)
    assert claimed is not None

    worker = Worker(b, source_argv, poll_interval_s=0.01)
    worker.handle_one(claimed)
    assert (b.paths.inbox / f"{env.id}.json").is_file()


def test_source_argv_skips_cli_source(clients, monkeypatch) -> None:
    a, b = clients
    monkeypatch.setenv("BOT_COMS_NOTIFY_ARGV", json.dumps(["/bin/echo", "{source}"]))

    request = a.send(
        "b",
        "request",
        {"task": "x"},
        reply_to="a",
        headers={"source": "cli:session-1"},
    )
    claimed = b.claim(msg_id=request.id)
    assert claimed is not None
    b.ack(claimed, result={"ok": True})

    response = a.claim()
    assert response is not None
    worker = Worker(a, source_argv, poll_interval_s=0.01)
    worker.handle_one(response)
    assert not list(a.paths.inbox.glob("*.json"))


def test_summary_line_for_report_ack() -> None:
    assert (
        summary_line(
            {
                "intent": "ack",
                "slice": "S12",
                "status": "REVIEW",
                "verdict": "LANDED",
                "evidence": "tee ok exit=0",
            }
        )
        == "S12 LANDED — tee ok exit=0"
    )
    assert summary_line({"ok": True}) is None


def test_source_argv_heals_headerless_response_from_profile_env(
    clients, tmp_path, monkeypatch
) -> None:
    a, b = clients
    team_root = tmp_path / "team"
    team_root.mkdir()
    peers_yaml = team_root / "peers.yaml"
    peers_yaml.write_text(
        "peers:\n  - id: a\n    profile: project-manager\n",
        encoding="utf-8",
    )
    profile_env = tmp_path / ".hermes" / "profiles" / "project-manager" / ".env"
    profile_env.parent.mkdir(parents=True)
    profile_env.write_text(
        'BOT_COMS_DEFAULT_SOURCE=discord:healed-channel\n'
        'BOT_COMS_NOTIFY_ARGV=["/bin/echo", "{source}"]\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("BOT_COMS_DEFAULT_SOURCE", raising=False)
    monkeypatch.delenv("BOT_COMS_NOTIFY_ARGV", raising=False)
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_PEERS_YAML", str(peers_yaml))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    request = a.send("b", "request", {"task": "x"}, reply_to="a")
    claimed = b.claim(msg_id=request.id)
    assert claimed is not None
    b.ack(claimed, result={"ok": True})

    response = a.claim()
    assert response is not None
    response.envelope.headers = None

    worker = Worker(a, source_argv, poll_interval_s=0.01)
    worker.handle_one(response)
    assert not list(a.paths.inbox.glob("*.json"))
    assert list(a.paths.acked.glob(f"{response.envelope.id}.json"))
