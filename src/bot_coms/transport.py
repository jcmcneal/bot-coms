"""Transport protocol ABC. FsTransport now; HTTP later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from bot_coms.client import Client
from bot_coms.types import ClaimedMessage, Envelope, MessageState


class Transport(ABC):
    @abstractmethod
    def send(self, to: str, type: str, payload: dict[str, Any], **kwargs: Any) -> Envelope: ...

    @abstractmethod
    def claim(self, msg_id: str | None = None) -> ClaimedMessage | None: ...

    @abstractmethod
    def ack(self, claimed: ClaimedMessage, *, result: dict[str, Any] | None = None) -> None: ...

    @abstractmethod
    def nack(self, claimed: ClaimedMessage, *, error: str, retryable: bool = True) -> None: ...

    @abstractmethod
    def status(self, msg_id: str) -> MessageState: ...


class FsTransport(Transport):
    def __init__(self, client: Client) -> None:
        self.client = client

    def send(self, to: str, type: str, payload: dict[str, Any], **kwargs: Any) -> Envelope:
        return self.client.send(to, type, payload, **kwargs)

    def claim(self, msg_id: str | None = None) -> ClaimedMessage | None:
        return self.client.claim(msg_id=msg_id)

    def ack(self, claimed: ClaimedMessage, *, result: dict[str, Any] | None = None) -> None:
        self.client.ack(claimed, result=result)

    def nack(self, claimed: ClaimedMessage, *, error: str, retryable: bool = True) -> None:
        self.client.nack(claimed, error=error, retryable=retryable)

    def status(self, msg_id: str) -> MessageState:
        return self.client.status(msg_id)
