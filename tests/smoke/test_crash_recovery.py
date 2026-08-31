from __future__ import annotations

import pytest


@pytest.mark.timeout(10)
def test_crash_recovery_single_effect(clients, spool_root, clock, config) -> None:
    a, b = clients
    effects = []
    env = a.send("b", "request", {"x": 1}, idempotency_key="crash-key")
    claimed = b.claim()
    assert claimed is not None
    effects.append("handler")
    # crash before ack: drop claimed, leave processing + lease
    clock.advance(config.lease_timeout_s + 1)
    claimed2 = b.claim()
    assert claimed2 is not None
    assert claimed2.envelope.id == env.id
    assert claimed2.envelope.attempt == 0
    b.ack(claimed2, result={"ok": True})
    assert effects == ["handler"]
    assert (spool_root / "b" / "acked" / f"{env.id}.json").is_file()
    assert not (spool_root / "b" / "processing" / f"{env.id}.json").exists()
