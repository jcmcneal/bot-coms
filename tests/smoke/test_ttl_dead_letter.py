from __future__ import annotations

from bot_coms import Client, HandlerError, SpoolConfig, Worker
from bot_coms.atomic import read_json


class _ZeroRng:
    def uniform(self, a: float, b: float) -> float:
        return 0.0


def test_ttl_expired_dead_letter(clients, spool_root, clock) -> None:
    a, b = clients
    env = a.send("b", "event", {"x": 1}, ttl_s=1)
    clock.advance(2)
    assert b.claim() is None
    wrapped = read_json(spool_root / "b" / "dead-letter" / f"{env.id}.json")
    assert wrapped["reason"] == "expired"


def test_max_attempts_dead_letter(spool_root, clock) -> None:
    cfg = SpoolConfig()
    cfg.retry.max_attempts = 2
    cfg.retry.rng = _ZeroRng()  # type: ignore[assignment]
    a = Client(spool_root, "a", clock=clock, config=cfg)
    b = Client(spool_root, "b", clock=clock, config=cfg)
    env = a.send("b", "request", {"x": 1})

    def handler(claimed):
        raise HandlerError("always", retryable=True)

    Worker(b, handler, poll_interval_s=0.01).run_until_idle(idle_rounds=8)
    wrapped = read_json(spool_root / "b" / "dead-letter" / f"{env.id}.json")
    assert wrapped["reason"] == "max_attempts"
    assert wrapped["envelope"]["attempt"] == 2
