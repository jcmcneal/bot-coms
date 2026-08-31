from __future__ import annotations

from bot_coms import Worker


def test_dedupe_single_side_effect(clients) -> None:
    a, b = clients
    a.send("b", "request", {"x": 1}, idempotency_key="same")
    a.send("b", "request", {"x": 2}, idempotency_key="same")
    hits = {"n": 0}

    def handler(claimed):
        hits["n"] += 1
        return {"n": hits["n"]}

    Worker(b, handler, poll_interval_s=0.01).run_until_idle(idle_rounds=8)
    assert hits["n"] == 1
    assert len(list(b.paths.acked.glob("*.json"))) == 2
