"""SpoolConfig / RetryConfig from env and spool.json."""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass
class RetryConfig:
    base_s: float = 1.0
    max_s: float = 60.0
    multiplier: float = 2.0
    max_attempts: int = 5
    rng: random.Random | random.SystemRandom | None = None

    def jitter_rng(self) -> random.Random | random.SystemRandom:
        return self.rng if self.rng is not None else random.SystemRandom()


@dataclass
class SpoolConfig:
    dir_mode: int = 0o700
    file_mode: int = 0o600
    max_payload_bytes: int = 1 << 20
    default_ttl_s: float = 86400.0
    lease_timeout_s: float = 60.0
    heartbeat_interval_s: float = 15.0
    idempotency_retention_s: float = 7 * 86400.0
    allow_foreign_owner: bool = False
    poll_interval_s: float = 0.05
    retry: RetryConfig = field(default_factory=RetryConfig)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "dir_mode": self.dir_mode,
            "file_mode": self.file_mode,
            "max_payload_bytes": self.max_payload_bytes,
            "default_ttl_s": self.default_ttl_s,
            "lease_timeout_s": self.lease_timeout_s,
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "idempotency_retention_s": self.idempotency_retention_s,
            "allow_foreign_owner": self.allow_foreign_owner,
            "poll_interval_s": self.poll_interval_s,
            "retry": {
                "base_s": self.retry.base_s,
                "max_s": self.retry.max_s,
                "multiplier": self.retry.multiplier,
                "max_attempts": self.retry.max_attempts,
            },
        }


def load_spool_json(path: Path, base: SpoolConfig | None = None) -> SpoolConfig:
    cfg = base or SpoolConfig()
    if not path.is_file():
        return cfg
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return cfg
    retry_data = data.get("retry") or {}
    retry = replace(
        cfg.retry,
        **{k: retry_data[k] for k in ("base_s", "max_s", "multiplier", "max_attempts") if k in retry_data},
    )
    fields = {
        k: data[k]
        for k in (
            "dir_mode",
            "file_mode",
            "max_payload_bytes",
            "default_ttl_s",
            "lease_timeout_s",
            "heartbeat_interval_s",
            "idempotency_retention_s",
            "allow_foreign_owner",
            "poll_interval_s",
        )
        if k in data
    }
    return replace(cfg, retry=retry, **fields)


def env_defaults() -> dict[str, str | None]:
    return {
        "spool_root": os.environ.get("BOT_COMS_SPOOL_ROOT"),
        "peer_id": os.environ.get("BOT_COMS_PEER_ID"),
        "token": os.environ.get("BOT_COMS_TOKEN"),
    }
