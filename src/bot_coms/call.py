"""Higher-level request / fire helpers for callers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from bot_coms.client import Client
from bot_coms.envelope import envelope_from_dict, generate_ulid
from bot_coms.atomic import read_json
from bot_coms.lifecycle import status
from bot_coms.spool import require_peer
from bot_coms.types import BotComsError, Envelope, PermissionDenied


class CallTimeout(BotComsError):
    """No correlated result arrived within the wait window."""

    def __init__(self, correlation_id: str, *, timeout_s: float) -> None:
        super().__init__(f"timed out waiting for correlation_id={correlation_id} after {timeout_s}s")
        self.correlation_id = correlation_id
        self.timeout_s = timeout_s


class CallDeadLetter(BotComsError):
    """Outbound or correlated message reached a terminal dead-letter state."""

    def __init__(self, correlation_id: str, *, reason: str, message_id: str = "") -> None:
        detail = f"dead-letter reason={reason}"
        if message_id:
            detail = f"{detail} message_id={message_id}"
        super().__init__(f"call failed for correlation_id={correlation_id}: {detail}")
        self.correlation_id = correlation_id
        self.reason = reason
        self.message_id = message_id


@dataclass
class CallResult:
    correlation_id: str
    payload: dict[str, Any]
    response_envelope: Envelope
    outbound_envelope: Envelope


def fire(
    client: Client,
    to: str,
    payload: dict[str, Any],
    *,
    msg_type: str = "event",
    ttl_s: float | None = None,
    headers: dict[str, str] | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    priority: int = 0,
) -> Envelope:
    """Fire-and-forget send with explicit no-wait semantics."""
    return client.send(
        to,
        msg_type,
        payload,
        ttl_s=ttl_s,
        headers=headers,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        priority=priority,
    )


def request(
    client: Client,
    to: str,
    payload: dict[str, Any],
    *,
    timeout_s: float = 120.0,
    ttl_s: float | None = None,
    headers: dict[str, str] | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    priority: int = 0,
) -> CallResult:
    """Send a request, wait for the correlated result, and silently ack the terminal response."""
    corr = correlation_id or generate_ulid(client.clock.now())
    outbound = client.send(
        to,
        "request",
        payload,
        reply_to=client.peer_id,
        correlation_id=corr,
        ttl_s=ttl_s,
        headers=headers,
        idempotency_key=idempotency_key,
        priority=priority,
    )
    response = wait_for_result(
        client,
        corr,
        timeout_s=timeout_s,
        outbound=outbound,
    )
    return CallResult(
        correlation_id=corr,
        payload=response.payload,
        response_envelope=response,
        outbound_envelope=outbound,
    )


def wait_for_result(
    client: Client,
    correlation_id: str,
    *,
    timeout_s: float = 120.0,
    outbound: Envelope | None = None,
) -> Envelope:
    """Poll for a correlated response, inbox-fallback claim, and silent terminal ack."""
    deadline = time.monotonic() + max(timeout_s, 0.0)
    response: Envelope | None = None
    while response is None:
        response = client.poll_result(correlation_id, timeout_s=0)
        if response is not None:
            break
        response = find_inbox_response(client, correlation_id)
        if response is not None:
            break
        if outbound is not None:
            raise_if_dead_letter(client, outbound, correlation_id)
        if timeout_s <= 0 or time.monotonic() >= deadline:
            if outbound is not None:
                raise_if_dead_letter(client, outbound, correlation_id)
            raise CallTimeout(correlation_id, timeout_s=timeout_s)
        time.sleep(min(client.config.poll_interval_s, 0.05))

    return ack_terminal_response(client, correlation_id, hint=response)


def read_inbox_envelope(client: Client, path) -> Envelope | None:
    try:
        return envelope_from_dict(
            read_json(path),
            max_payload_bytes=client.config.max_payload_bytes,
        )
    except (OSError, ValueError):
        return None


def find_inbox_response(client: Client, correlation_id: str) -> Envelope | None:
    for path in sorted(client.paths.inbox.glob("*.json"), key=lambda p: p.name):
        env = read_inbox_envelope(client, path)
        if env is None:
            continue
        if env.type == "response" and env.correlation_id == correlation_id:
            return env
    return None


def ack_terminal_response(
    client: Client,
    correlation_id: str,
    *,
    hint: Envelope | None = None,
) -> Envelope:
    """Claim a correlated response from inbox when present and ack without result."""
    inbox = find_inbox_response(client, correlation_id)
    if inbox is not None:
        claimed = client.claim(msg_id=inbox.id)
        if claimed is not None:
            client.ack(claimed)
            return claimed.envelope
    if hint is not None:
        return hint
    raise CallTimeout(correlation_id, timeout_s=0)


def raise_if_dead_letter(client: Client, outbound: Envelope, correlation_id: str) -> None:
    outbound_state = client.status(outbound.id)
    if outbound_state.location == "dead-letter":
        raise CallDeadLetter(
            correlation_id,
            reason=outbound_state.dead_letter_reason or "unknown",
            message_id=outbound.id,
        )
    if outbound.to != client.peer_id:
        receiver = require_peer(client.spool_root, outbound.to)
        receiver_state = status(receiver, outbound.id, config=client.config)
        if receiver_state.location == "dead-letter":
            raise CallDeadLetter(
                correlation_id,
                reason=receiver_state.dead_letter_reason or "unknown",
                message_id=outbound.id,
            )
    for path in client.paths.dead_letter.glob("*.json"):
        try:
            wrapper = read_json(path)
        except OSError:
            continue
        env_raw = wrapper.get("envelope") if isinstance(wrapper, dict) else None
        if not isinstance(env_raw, dict):
            continue
        env_id = env_raw.get("correlation_id")
        if env_id == correlation_id:
            reason = wrapper.get("reason") if isinstance(wrapper, dict) else "unknown"
            msg_id = env_raw.get("id") if isinstance(env_raw.get("id"), str) else ""
            raise CallDeadLetter(correlation_id, reason=str(reason or "unknown"), message_id=msg_id)


def map_call_error(exc: BaseException) -> tuple[str, str]:
    """Map library errors to safe structured (error, reason) pairs without payload leakage."""
    if isinstance(exc, CallTimeout):
        return str(exc), "timeout"
    if isinstance(exc, CallDeadLetter):
        return str(exc), "dead_letter"
    if isinstance(exc, PermissionDenied):
        return str(exc), "permission"
    if isinstance(exc, BotComsError):
        return str(exc), "handler_error"
    return str(exc), "error"
