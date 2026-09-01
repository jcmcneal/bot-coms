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


def test_ack_response_with_result_does_not_create_receipt_loop(clients, spool_root) -> None:
    a, b = clients
    request = a.send("b", "request", {"need": "result"}, reply_to="a")
    claimed_request = b.claim()
    assert claimed_request is not None
    b.ack(claimed_request, result={"answer": 42})

    response = a.claim()
    assert response is not None
    assert response.envelope.type == "response"
    a.ack(response, result={"ack": "received"})

    assert not list((spool_root / "b" / "inbox").glob("*.json"))


def test_ack_result_preserves_original_channel_routing_headers(clients) -> None:
    a, b = clients
    request = a.send(
        "b",
        "request",
        {"need": "report"},
        reply_to="a",
        headers={"discord_channel_id": "channel-123", "discord_thread_id": "thread-456"},
    )
    claimed = b.claim(msg_id=request.id)
    assert claimed is not None
    b.ack(claimed, result={"status": "complete"})

    response = a.claim()
    assert response is not None
    assert response.envelope.headers == {
        "discord_channel_id": "channel-123",
        "discord_thread_id": "thread-456",
    }
