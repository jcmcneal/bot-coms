from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot_coms import Client, FakeClock, SpoolConfig, init_spool


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc))


@pytest.fixture
def config() -> SpoolConfig:
    cfg = SpoolConfig()
    cfg.poll_interval_s = 0.01
    cfg.heartbeat_interval_s = 0.05
    cfg.lease_timeout_s = 60.0
    return cfg


@pytest.fixture
def spool_root(tmp_path: Path, clock: FakeClock, config: SpoolConfig) -> Path:
    root = tmp_path / "spool"
    init_spool(root, ["a", "b"], config=config, clock=clock)
    return root


@pytest.fixture
def clients(spool_root: Path, clock: FakeClock, config: SpoolConfig) -> tuple[Client, Client]:
    a = Client(spool_root, "a", clock=clock, config=config)
    b = Client(spool_root, "b", clock=clock, config=config)
    return a, b
