from __future__ import annotations


def test_one_way(clients, spool_root) -> None:
    a, b = clients
    env = a.send("b", "request", {"hello": "world"}, reply_to="a")
    assert (spool_root / "a" / "outbox" / f"{env.id}.json").is_file()
    claimed = b.claim()
    assert claimed is not None
    assert claimed.envelope.id == env.id
    b.ack(claimed, result={"ok": True})
    assert (spool_root / "b" / "acked" / f"{env.id}.json").is_file()
    assert not (spool_root / "b" / "processing" / f"{env.id}.json").exists()
    assert a.poll_result(env.correlation_id) is not None
