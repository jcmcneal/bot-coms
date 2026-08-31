from __future__ import annotations

from bot_coms.atomic import read_json


def test_ack_result_files(clients, spool_root) -> None:
    a, b = clients
    env = a.send("b", "request", {"need": "result"}, reply_to="a", correlation_id="corr-result")
    claimed = b.claim()
    assert claimed is not None
    assert claimed.path.parent.name == "processing"
    b.ack(claimed, result={"answer": 42})
    assert (spool_root / "b" / "acked" / f"{env.id}.json").is_file()
    assert not list((spool_root / "b" / "processing").glob("*.json"))
    result_b = read_json(spool_root / "b" / "results" / "corr-result.json")
    result_a = read_json(spool_root / "a" / "results" / "corr-result.json")
    assert result_b["payload"] == {"answer": 42}
    assert result_a["type"] == "response"
    replies = list((spool_root / "a" / "inbox").glob("*.json"))
    assert len(replies) == 1
