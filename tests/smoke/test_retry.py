from __future__ import annotations

from bot_coms import Client, HandlerError, SpoolConfig, Worker
from bot_coms.atomic import read_json


class _ZeroRng:
    def uniform(self, a: float, b: float) -> float:
        return 0.0


def test_retry_attempt_and_backoff(spool_root, clock) -> None:
    cfg = SpoolConfig()
    cfg.retry.max_attempts = 5
    cfg.retry.rng = _ZeroRng()  # type: ignore[assignment]
    cfg.poll_interval_s = 0.01
    a = Client(spool_root, "a", clock=clock, config=cfg)
    b = Client(spool_root, "b", clock=clock, config=cfg)
    env = a.send("b", "request", {"n": 1})
    fails = {"n": 0}

    def handler(claimed):
        fails["n"] += 1
        if fails["n"] < 3:
            raise HandlerError("boom", retryable=True)
        return {"ok": True}

    worker = Worker(b, handler, poll_interval_s=0.01)
    worker.run_until_idle(idle_rounds=8)
    assert fails["n"] == 3
    acked = read_json(spool_root / "b" / "acked" / f"{env.id}.json")
    assert acked["attempt"] == 2
