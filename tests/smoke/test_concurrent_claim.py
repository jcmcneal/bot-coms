from __future__ import annotations

import threading


def test_concurrent_claim_one_winner(clients) -> None:
    a, b = clients
    env = a.send("b", "event", {"x": 1})
    barrier = threading.Barrier(2)
    got: list = []

    def try_claim() -> None:
        barrier.wait()
        got.append(b.claim())

    t1 = threading.Thread(target=try_claim)
    t2 = threading.Thread(target=try_claim)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    winners = [c for c in got if c is not None]
    assert len(winners) == 1
    assert winners[0].envelope.id == env.id
