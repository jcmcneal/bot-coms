from __future__ import annotations

from bot_coms.idempotency import IdempotencyStore
from bot_coms.types import FakeClock


def test_begin_complete_replay(tmp_path, clock: FakeClock) -> None:
    store = IdempotencyStore(tmp_path / "id.sqlite")
    first = store.begin("b", "k1", clock)
    assert first.status == "started"
    store.complete("b", "k1", {"ok": True}, clock)
    second = store.begin("b", "k1", clock)
    assert second.status == "completed"
    assert second.result == {"ok": True}


def test_concurrent_begin_in_progress(tmp_path, clock: FakeClock) -> None:
    store = IdempotencyStore(tmp_path / "id.sqlite")
    assert store.begin("b", "k1", clock).status == "started"
    assert store.begin("b", "k1", clock).status == "in_progress"
    store.abort("b", "k1")
    assert store.begin("b", "k1", clock).status == "started"
