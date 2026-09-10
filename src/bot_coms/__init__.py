"""bot-coms public API."""

from __future__ import annotations

from bot_coms.client import Client
from bot_coms.config import RetryConfig, SpoolConfig
from bot_coms.call import CallDeadLetter, CallResult, CallTimeout, fire, map_call_error, request
from bot_coms.delegate import await_child, delegate_and_ack, forward_send
from bot_coms.headers import forward_headers, format_source, SOURCE_HEADER
from bot_coms.notify import completion_sink_argv, source_argv
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
    SkipMessage,
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
    "CallResult",
    "CallTimeout",
    "CallDeadLetter",
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
    "SkipMessage",
    "request",
    "fire",
    "forward_send",
    "await_child",
    "delegate_and_ack",
    "map_call_error",
    "forward_headers",
    "format_source",
    "SOURCE_HEADER",
    "completion_sink_argv",
    "source_argv",
    "init_spool",
    "__version__",
]
