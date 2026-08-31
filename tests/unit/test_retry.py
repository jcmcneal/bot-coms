from __future__ import annotations

import random

from bot_coms.config import RetryConfig
from bot_coms.retry import backoff_delay, is_expired, is_visible
from bot_coms.types import Envelope


def test_backoff_full_jitter_bounds() -> None:
    cfg = RetryConfig(base_s=1, max_s=8, multiplier=2.0, rng=random.Random(0))
    for attempt in range(0, 6):
        d = backoff_delay(attempt, cfg)
        cap = min(cfg.max_s, cfg.base_s * (cfg.multiplier ** attempt))
        assert 0 <= d <= cap


def test_visibility_and_expiry(clock) -> None:
    env = Envelope(
        schema_version="1.0",
        id="01TESTTESTTESTTESTTESTTEST",
        idempotency_key="k",
        correlation_id="c",
        from_peer="a",
        to="b",
        type="event",
        payload={},
        created_at="2026-08-31T18:00:00.000Z",
        expires_at="2026-08-31T18:00:10.000Z",
        next_visible_at="2026-08-31T18:00:05.000Z",
    )
    assert not is_visible(env, clock.now())
    assert not is_expired(env, clock.now())
    clock.advance(6)
    assert is_visible(env, clock.now())
    clock.advance(10)
    assert is_expired(env, clock.now())
