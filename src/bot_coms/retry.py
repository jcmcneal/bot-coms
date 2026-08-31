"""Backoff, visibility, and TTL checks."""

from __future__ import annotations

from datetime import datetime, timezone

from bot_coms.config import RetryConfig
from bot_coms.envelope import parse_ts
from bot_coms.types import Envelope


def backoff_delay(attempt: int, cfg: RetryConfig) -> float:
    cap = min(cfg.max_s, cfg.base_s * (cfg.multiplier ** max(attempt, 0)))
    return float(cfg.jitter_rng().uniform(0, cap))


def is_expired(env: Envelope, now: datetime) -> bool:
    return now >= parse_ts(env.expires_at)


def is_visible(env: Envelope, now: datetime) -> bool:
    if env.next_visible_at is None:
        return True
    return now >= parse_ts(env.next_visible_at)


def aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now
