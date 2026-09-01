"""Delegation helpers for intermediate peers."""

from __future__ import annotations

from typing import Any

from bot_coms.call import wait_for_result
from bot_coms.client import Client
from bot_coms.headers import forward_headers
from bot_coms.types import ClaimedMessage, Envelope


def forward_send(
    client: Client,
    claimed: ClaimedMessage,
    to: str,
    payload: dict[str, Any],
    *,
    ttl_s: float | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    priority: int = 0,
) -> Envelope:
    """Re-assign work downstream while preserving opaque routing headers."""
    return client.send(
        to,
        "request",
        payload,
        reply_to=client.peer_id,
        headers=forward_headers(claimed.envelope),
        ttl_s=ttl_s,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        priority=priority,
    )


def await_child(
    client: Client,
    correlation_id: str,
    *,
    timeout_s: float = 120.0,
    outbound: Envelope | None = None,
) -> dict[str, Any]:
    """Wait for a child result, claim/ack the terminal response, return payload."""
    response = wait_for_result(
        client,
        correlation_id,
        timeout_s=timeout_s,
        outbound=outbound,
    )
    return dict(response.payload)


def delegate_and_ack(
    client: Client,
    claimed: ClaimedMessage,
    to: str,
    payload: dict[str, Any],
    *,
    timeout_s: float = 120.0,
    ttl_s: float | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Forward to a child peer, wait for the result, and fold up exactly once."""
    child = forward_send(
        client,
        claimed,
        to,
        payload,
        ttl_s=ttl_s,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    child_payload = await_child(
        client,
        child.correlation_id,
        timeout_s=timeout_s,
        outbound=child,
    )
    client.ack(claimed, result=child_payload)
