from __future__ import annotations


def test_bidirectional(clients) -> None:
    a, b = clients
    e1 = a.send(
        "b",
        "request",
        {"q": 1},
        reply_to="a",
        correlation_id="corr-ab",
        idempotency_key="key-ab",
    )
    c1 = b.claim()
    assert c1 is not None
    b.ack(c1, result={"from": "b"})
    r1 = a.claim()
    assert r1 is not None
    assert r1.envelope.correlation_id == "corr-ab"
    assert r1.envelope.type == "response"
    a.ack(r1)

    e2 = b.send(
        "a",
        "request",
        {"q": 2},
        reply_to="b",
        correlation_id="corr-ba",
        idempotency_key="key-ba",
    )
    c2 = a.claim()
    assert c2 is not None
    a.ack(c2, result={"from": "a"})
    r2 = b.claim()
    assert r2 is not None
    assert r2.envelope.correlation_id == "corr-ba"
    b.ack(r2)
    assert e1.correlation_id == "corr-ab"
    assert e2.correlation_id == "corr-ba"
