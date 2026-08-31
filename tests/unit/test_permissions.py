from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bot_coms import Client, PermissionDenied, ValidationError
from bot_coms.atomic import write_json_atomic
from bot_coms.permissions import hash_token
from bot_coms.types import FakeClock


def test_rejects_dotdot_peer(clients, spool_root) -> None:
    a, _ = clients
    with pytest.raises(ValidationError):
        a.send("..", "event", {"x": 1})


def test_symlink_escape_fails_closed(spool_root: Path, clock: FakeClock, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = spool_root / "escaped"
    dest.symlink_to(outside)
    with pytest.raises(PermissionDenied):
        write_json_atomic(
            dest,
            "x.json",
            {"a": 1},
            tmp_dir=spool_root / "b" / "tmp",
            root=spool_root,
        )


def test_bad_token_denied(spool_root: Path, clock: FakeClock, config) -> None:
    allow = {"a": {"token_hash": hash_token("secret"), "can_send_to": ["b"]}}
    (spool_root / "a" / "state" / "allowlist.json").write_text(json.dumps(allow), encoding="utf-8")
    bad = Client(spool_root, "a", token="nope", clock=clock, config=config)
    with pytest.raises(PermissionDenied):
        bad.send("b", "event", {"x": 1})
    good = Client(spool_root, "a", token="secret", clock=clock, config=config)
    env = good.send("b", "event", {"x": 1})
    assert (spool_root / "b" / "inbox" / f"{env.id}.json").is_file()


def test_foreign_owner_refused(spool_root: Path, clock: FakeClock, config, monkeypatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(PermissionDenied):
        Client(spool_root, "a", clock=clock, config=config)


def test_created_modes_are_owner_only(spool_root: Path) -> None:
    inbox = spool_root / "a" / "inbox"
    assert inbox.stat().st_mode & 0o777 == 0o700
