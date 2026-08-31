"""bot-coms public API."""

from __future__ import annotations

from bot_coms.client import Client
from bot_coms.config import RetryConfig, SpoolConfig
from bot_coms.spool import init_spool
from bot_coms.transport import FsTransport, Transport
from bot_coms.types import (
    BotComsError,
    ClaimedMessage,
    ClaimLost,
    DuplicateIdempotency,
    Envelope,
    Expired,
    FakeClock,
    HandlerError,
    MessageState,
    PermissionDenied,
    PoisonError,
    SystemClock,
    ValidationError,
)
from bot_coms.worker import Worker

__version__ = "0.1.0"

__all__ = [
    "Client",
    "Worker",
    "Envelope",
    "ClaimedMessage",
    "MessageState",
    "SpoolConfig",
    "RetryConfig",
    "Transport",
    "FsTransport",
    "FakeClock",
    "SystemClock",
    "BotComsError",
    "ValidationError",
    "PermissionDenied",
    "ClaimLost",
    "Expired",
    "DuplicateIdempotency",
    "HandlerError",
    "PoisonError",
    "init_spool",
    "__version__",
]
