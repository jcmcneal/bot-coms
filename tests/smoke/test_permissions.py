from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bot_coms import Client, PermissionDenied, ValidationError
from bot_coms.permissions import hash_token


def test_permissions_fail_closed(clients, spool_root, clock, config, tmp_path: Path) -> None:
    a, _b = clients
    with pytest.raises(ValidationError):
        a.send("../etc", "event", {"x": 1})

    outside = tmp_path / "outside"
    outside.mkdir()
    inbox = spool_root / "b" / "inbox"
    for child in inbox.iterdir():
        child.unlink()
    inbox.rmdir()
    inbox.symlink_to(outside)
    with pytest.raises(PermissionDenied):
        a.send("b", "event", {"x": 1})

    inbox.unlink()
    inbox.mkdir()
    os.chmod(inbox, 0o700)

    allow = {"a": {"token_hash": hash_token("secret"), "can_send_to": ["b"]}}
    (spool_root / "a" / "state" / "allowlist.json").write_text(json.dumps(allow), encoding="utf-8")
    bad = Client(spool_root, "a", token="wrong", clock=clock, config=config)
    with pytest.raises(PermissionDenied):
        bad.send("b", "event", {"x": 1})
