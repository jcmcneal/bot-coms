from __future__ import annotations

import threading
import time

import pytest

from bot_coms import Client, Worker


@pytest.mark.timeout(15)
def test_live_worker_restart_drains(clients, spool_root, clock, config) -> None:
    a, b = clients
    for i in range(12):
        a.send("b", "event", {"i": i}, idempotency_key=f"live-{i}")

    seen: list[int] = []

    def handler(claimed):
        seen.append(int(claimed.envelope.payload["i"]))
        time.sleep(0.02)
        return {"i": claimed.envelope.payload["i"]}

    stop = threading.Event()
    w1 = Worker(b, handler, poll_interval_s=0.01)
    thread = threading.Thread(target=w1.run_forever, kwargs={"stop": stop}, daemon=True)
    thread.start()
    deadline = time.time() + 3
    while time.time() < deadline and len(list((spool_root / "b" / "acked").glob("*.json"))) < 3:
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()

    b2 = Client(spool_root, "b", clock=clock, config=config)
    Worker(b2, handler, poll_interval_s=0.01).run_until_idle(idle_rounds=10)

    processing = list((spool_root / "b" / "processing").glob("*.json"))
    leases = list((spool_root / "b" / "state" / "leases").glob("*.json"))
    assert processing == []
    assert leases == []
    terminal = list((spool_root / "b" / "acked").glob("*.json")) + list(
        (spool_root / "b" / "dead-letter").glob("*.json")
    )
    assert len(terminal) == 12
