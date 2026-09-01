from __future__ import annotations

from bot_coms.lease import reclaim_stale, write_lease
from bot_coms.paths import peer_paths


def test_reclaim_after_timeout(spool_root, clock, config) -> None:
    b = peer_paths(spool_root, "b")
    msg = b.processing / "01TESTTESTTESTTESTTESTTEST.json"
    msg.write_text("{}", encoding="utf-8")
    write_lease(b, "01TESTTESTTESTTESTTESTTEST", worker_id="w", clock=clock, config=config)
    assert reclaim_stale(b, clock, config) == []
    clock.advance(config.lease_timeout_s + 1)
    got = reclaim_stale(b, clock, config)
    assert got == ["01TESTTESTTESTTESTTESTTEST"]
    assert (b.inbox / msg.name).is_file()
    assert not msg.exists()
    assert not b.lease_path("01TESTTESTTESTTESTTESTTEST").exists()


def test_client_reclaim_sweeps_without_claiming(clients, spool_root, clock, config) -> None:
    _a, b = clients
    env = b.send("b", "event", {"recover": True})
    claimed = b.claim()
    assert claimed is not None
    clock.advance(config.lease_timeout_s + 1)

    assert b.reclaim_stale() == [env.id]
    assert (spool_root / "b" / "inbox" / f"{env.id}.json").is_file()
