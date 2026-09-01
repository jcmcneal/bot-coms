from __future__ import annotations

import threading

from bot_coms import Worker
from bot_coms.delegate import delegate_and_ack


def test_delegate_three_peer_fold_up_preserves_source(three_clients) -> None:
    pm, swe, sub = three_clients

    def sub_handler(_claimed):
        return {"status": "done", "from": "sub"}

    sub_worker = threading.Thread(
        target=lambda: Worker(sub, sub_handler, poll_interval_s=0.01).run_until_idle(idle_rounds=10),
        daemon=True,
    )
    sub_worker.start()

    def swe_handler(claimed):
        delegate_and_ack(swe, claimed, "c", {"task": "run"}, timeout_s=5)

    swe_worker = threading.Thread(
        target=lambda: Worker(swe, swe_handler, poll_interval_s=0.01).run_until_idle(idle_rounds=10),
        daemon=True,
    )
    swe_worker.start()

    result = pm.request(
        "b",
        {"assign": "work"},
        timeout_s=5,
        headers={"source": "discord:channel-123:thread-456"},
    )
    swe_worker.join(timeout=2)
    sub_worker.join(timeout=2)

    assert result.payload == {"status": "done", "from": "sub"}
    assert result.response_envelope.headers == {"source": "discord:channel-123:thread-456"}
    assert not list(pm.paths.inbox.glob("*.json"))
    assert not list(swe.paths.processing.glob("*.json"))


def test_forward_send_copies_headers(clients) -> None:
    a, b = clients
    request = a.send(
        "b",
        "request",
        {"task": "x"},
        reply_to="a",
        headers={"source": "telegram:99"},
    )
    claimed = b.claim(msg_id=request.id)
    assert claimed is not None

    from bot_coms.delegate import forward_send

    child = forward_send(b, claimed, "a", {"delegated": True})
    assert child.headers == {"source": "telegram:99"}
    assert child.reply_to == "b"
    b.release(claimed)
