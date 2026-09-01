"""Public types, errors, and the injectable clock."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


SCHEMA_VERSION = "1.0"
PROTOCOL_ID = "bot-coms"
PEER_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MESSAGE_TYPES = frozenset({"request", "response", "event"})
DEAD_LETTER_REASONS = frozenset(
    {"expired", "max_attempts", "validation", "permission", "handler_error", "poison"}
)


class BotComsError(Exception):
    """Base error for the library."""


class ValidationError(BotComsError):
    pass


class PermissionDenied(BotComsError):
    pass


class ClaimLost(BotComsError):
    pass


class Expired(BotComsError):
    pass


class DuplicateIdempotency(BotComsError):
    pass


class PeerNotFound(BotComsError):
    pass


class HandlerError(BotComsError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class PoisonError(HandlerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class SkipMessage(BotComsError):
    """Handler declines this message; Worker releases it back to inbox unchanged."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Deterministic clock for smoke tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        self._now = when.astimezone(timezone.utc)


@dataclass
class Envelope:
    schema_version: str
    id: str
    idempotency_key: str
    correlation_id: str
    from_peer: str
    to: str
    type: str
    payload: dict[str, Any]
    created_at: str
    expires_at: str
    attempt: int = 0
    reply_to: str | None = None
    next_visible_at: str | None = None
    priority: int = 0
    headers: dict[str, str] | None = None
    receipt: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "from": self.from_peer,
            "to": self.to,
            "type": self.type,
            "payload": self.payload,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "attempt": self.attempt,
        }
        if self.reply_to is not None:
            data["reply_to"] = self.reply_to
        if self.next_visible_at is not None:
            data["next_visible_at"] = self.next_visible_at
        if self.priority:
            data["priority"] = self.priority
        if self.headers:
            data["headers"] = self.headers
        if self.receipt:
            data["receipt"] = self.receipt
        return data


@dataclass
class ClaimedMessage:
    envelope: Envelope
    peer_id: str
    path: Path
    worker_id: str


@dataclass
class MessageState:
    id: str
    location: str
    envelope: Envelope | None = None
    dead_letter_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BeginResult:
    status: str  # started | completed | in_progress
    result: dict[str, Any] | None = None
    digest: str | None = None
