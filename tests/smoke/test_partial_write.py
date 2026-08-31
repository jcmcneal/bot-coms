from __future__ import annotations

import pytest

from bot_coms.atomic import set_fault_hook


def test_partial_write_leaves_inbox_clean(clients, spool_root) -> None:
    a, _b = clients

    def boom(stage: str) -> None:
        if stage == "before_rename":
            raise OSError("injected")

    set_fault_hook(boom)
    try:
        with pytest.raises(OSError):
            a.send("b", "event", {"x": 1})
    finally:
        set_fault_hook(None)

    assert list((spool_root / "b" / "inbox").glob("*.json")) == []
    assert list((spool_root / "b" / "tmp").glob("*")) == []
    assert list((spool_root / "a" / "outbox").glob("*.json")) == []
