from __future__ import annotations

import json
import threading

import pytest

from bot_coms import CallDeadLetter, CallTimeout, Worker
from bot_coms.call import wait_for_result


def _run_worker(client, handler, *, idle_rounds: int = 10) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: Worker(client, handler, poll_interval_s=0.01).run_until_idle(
            idle_rounds=idle_rounds
        ),
        daemon=True,
    )
    thread.start()
    return thread


def test_request_success_auto_acks_response(clients, spool_root) -> None:
    a, b = clients
    worker = _run_worker(b, lambda _claimed: {"answer": 42})
    result = a.request("b", {"q": 1}, timeout_s=5)
    worker.join(timeout=2)
    assert result.payload == {"answer": 42}
    assert result.outbound_envelope.reply_to == "a"
    assert result.response_envelope.type == "response"
    assert not list(a.paths.inbox.glob("*.json"))
    assert list(a.paths.acked.glob("*.json"))


def test_request_timeout(clients) -> None:
    a, b = clients
    del b
    with pytest.raises(CallTimeout):
        a.request("b", {"q": 1}, timeout_s=0.05)


def test_request_dead_letter(clients, clock) -> None:
    a, b = clients
    outbound = a.send(
        "b",
        "request",
        {"x": 1},
        reply_to="a",
        correlation_id="corr-dead",
        ttl_s=1,
    )
    clock.advance(2)
    assert b.claim() is None
    with pytest.raises(CallDeadLetter) as exc:
        wait_for_result(a, "corr-dead", timeout_s=0.05, outbound=outbound)
    assert exc.value.reason == "expired"


def test_request_duplicate_idempotency_key(clients) -> None:
    a, b = clients
    worker = _run_worker(b, lambda _claimed: {"v": 1})
    r1 = a.request("b", {"x": 1}, idempotency_key="dup-key", timeout_s=5)
    r2 = a.request("b", {"x": 2}, idempotency_key="dup-key", timeout_s=5)
    worker.join(timeout=2)
    assert r1.payload == {"v": 1}
    assert r2.payload == {"v": 1}


def test_fire_explicit_no_wait(clients, spool_root) -> None:
    a, b = clients
    env = a.fire("b", {"ping": 1})
    assert env.type == "event"
    assert env.reply_to is None
    claimed = b.claim()
    assert claimed is not None
    assert claimed.envelope.payload == {"ping": 1}
    assert (spool_root / "a" / "outbox" / f"{env.id}.json").is_file()


def test_request_inbox_fallback_without_poll_mirror(clients, spool_root) -> None:
    a, b = clients
    env = a.send("b", "request", {"need": "result"}, reply_to="a", correlation_id="corr-fallback")
    claimed = b.claim()
    assert claimed is not None
    b.ack(claimed, result={"via": "inbox"})
    mirror = spool_root / "a" / "results" / "corr-fallback.json"
    if mirror.is_file():
        mirror.unlink()
    result = wait_for_result(a, "corr-fallback", timeout_s=1, outbound=env)
    assert result.payload == {"via": "inbox"}
    assert not list(a.paths.inbox.glob("*.json"))
