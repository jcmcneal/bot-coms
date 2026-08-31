"""Public Client facade."""

from __future__ import annotations

import os
import secrets
import socket
import time
from pathlib import Path
from typing import Any

from bot_coms.config import SpoolConfig, load_spool_json
from bot_coms.idempotency import IdempotencyStore
from bot_coms.lifecycle import ack as lc_ack
from bot_coms.lifecycle import claim as lc_claim
from bot_coms.lifecycle import nack as lc_nack
from bot_coms.lifecycle import poll_result as lc_poll_result
from bot_coms.lifecycle import release_to_inbox
from bot_coms.lifecycle import status as lc_status
from bot_coms.paths import PeerPaths
from bot_coms.permissions import assert_owner
from bot_coms.retry import is_expired, is_visible
from bot_coms.spool import init_spool, list_inbox, require_peer, send_message
from bot_coms.types import ClaimedMessage, Clock, Envelope, MessageState, SystemClock


class Client:
    def __init__(
        self,
        spool_root: Path,
        peer_id: str,
        *,
        token: str | None = None,
        config: SpoolConfig | None = None,
        clock: Clock | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.spool_root = Path(spool_root)
        self.peer_id = peer_id
        self.token = token
        self.clock = clock or SystemClock()
        base = config or SpoolConfig()
        self.paths: PeerPaths = require_peer(self.spool_root, peer_id)
        self.config = load_spool_json(self.paths.spool_json, base)
        if config is not None:
            # explicit config wins over snapshot, but keep retry rng from caller
            self.config = config
        assert_owner(self.paths.peer_root, allow_foreign_owner=self.config.allow_foreign_owner)
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(4)}"
        self._store = IdempotencyStore(self.paths.idempotency_db)

    @property
    def store(self) -> IdempotencyStore:
        return self._store

    def send(
        self,
        to: str,
        type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        reply_to: str | None = None,
        ttl_s: float | None = None,
        priority: int = 0,
        headers: dict[str, str] | None = None,
    ) -> Envelope:
        return send_message(
            self.spool_root,
            from_peer=self.peer_id,
            to=to,
            msg_type=type,
            payload=payload,
            config=self.config,
            clock=self.clock,
            token=self.token,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            reply_to=reply_to,
            ttl_s=ttl_s,
            priority=priority,
            headers=headers,
        )

    def receive(self, *, limit: int = 1) -> list[Envelope]:
        now = self.clock.now()
        eligible = [
            env
            for env in list_inbox(self.paths, config=self.config)
            if is_visible(env, now) and not is_expired(env, now)
        ]
        return eligible[: max(limit, 0)]

    def claim(self, msg_id: str | None = None) -> ClaimedMessage | None:
        return lc_claim(
            self.paths,
            config=self.config,
            clock=self.clock,
            worker_id=self.worker_id,
            msg_id=msg_id,
            store=self._store,
        )

    def ack(self, claimed: ClaimedMessage, *, result: dict[str, Any] | None = None) -> None:
        lc_ack(
            self.paths,
            claimed,
            config=self.config,
            clock=self.clock,
            store=self._store,
            result=result,
        )

    def nack(self, claimed: ClaimedMessage, *, error: str, retryable: bool = True) -> None:
        lc_nack(
            self.paths,
            claimed,
            error=error,
            config=self.config,
            clock=self.clock,
            store=self._store,
            retryable=retryable,
        )

    def release(self, claimed: ClaimedMessage) -> None:
        release_to_inbox(
            self.paths,
            claimed,
            config=self.config,
            clock=self.clock,
            store=self._store,
        )

    def status(self, msg_id: str) -> MessageState:
        return lc_status(self.paths, msg_id, config=self.config)

    def poll_result(self, correlation_id: str, *, timeout_s: float = 0) -> Envelope | None:
        deadline = time.monotonic() + max(timeout_s, 0)
        while True:
            found = lc_poll_result(self.paths, correlation_id, config=self.config)
            if found is not None or timeout_s <= 0 or time.monotonic() >= deadline:
                return found
            time.sleep(min(self.config.poll_interval_s, 0.05))

    def close(self) -> None:
        self._store.close()


def bootstrap_spool(
    spool_root: Path,
    peers: list[str],
    *,
    config: SpoolConfig | None = None,
    clock: Clock | None = None,
) -> Path:
    return init_spool(spool_root, peers, config=config, clock=clock)
