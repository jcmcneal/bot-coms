"""Worker run loop with lease heartbeat."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from bot_coms.client import Client
from bot_coms.types import ClaimedMessage, HandlerError, PoisonError, SkipMessage


class Worker:
    def __init__(
        self,
        client: Client,
        handler: Callable[[ClaimedMessage], dict[str, Any] | None],
        *,
        poll_interval_s: float | None = None,
    ) -> None:
        self.client = client
        self.handler = handler
        self.poll_interval_s = (
            poll_interval_s if poll_interval_s is not None else client.config.poll_interval_s
        )
        self._lock = threading.Lock()
        self._current: ClaimedMessage | None = None

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        interval = self.client.config.heartbeat_interval_s
        while not stop.wait(interval):
            with self._lock:
                current = self._current
            if current is None:
                continue
            try:
                from bot_coms.lease import write_lease

                write_lease(
                    self.client.paths,
                    current.envelope.id,
                    worker_id=self.client.worker_id,
                    clock=self.client.clock,
                    config=self.client.config,
                    heartbeat_only=True,
                )
            except Exception:
                continue

    def _set_current(self, claimed: ClaimedMessage | None) -> None:
        with self._lock:
            self._current = claimed

    def handle_one(self, claimed: ClaimedMessage) -> None:
        store = self.client.store
        store.gc(self.client.clock, self.client.config.idempotency_retention_s)
        begin = store.begin(
            self.client.peer_id, claimed.envelope.idempotency_key, self.client.clock
        )
        if begin.status == "completed":
            self.client.ack(claimed, result=begin.result)
            return
        if begin.status == "in_progress":
            self.client.nack(claimed, error="idempotency in_progress", retryable=True)
            return
        self._set_current(claimed)
        try:
            result = self.handler(claimed)
            self.client.ack(claimed, result=result)
        except SkipMessage:
            self.client.release(claimed)
        except PoisonError as exc:
            self.client.nack(claimed, error=str(exc), retryable=False)
        except HandlerError as exc:
            self.client.nack(claimed, error=str(exc), retryable=exc.retryable)
        except Exception as exc:
            self.client.nack(claimed, error=str(exc), retryable=True)
        finally:
            self._set_current(None)

    def run_forever(self, *, stop: threading.Event | None = None) -> None:
        own_stop = stop or threading.Event()
        hb_stop = threading.Event()
        hb = threading.Thread(target=self._heartbeat_loop, args=(hb_stop,), daemon=True)
        hb.start()
        try:
            while not own_stop.is_set():
                claimed = self.client.claim()
                if claimed is None:
                    own_stop.wait(self.poll_interval_s)
                    continue
                self.handle_one(claimed)
        finally:
            with self._lock:
                leftover = self._current
            if leftover is not None:
                try:
                    self.client.release(leftover)
                except Exception:
                    pass
                self._set_current(None)
            hb_stop.set()
            hb.join(timeout=1)

    def run_until_idle(self, *, stop: threading.Event | None = None, idle_rounds: int = 5) -> None:
        own_stop = stop or threading.Event()
        idle = 0
        hb_stop = threading.Event()
        hb = threading.Thread(target=self._heartbeat_loop, args=(hb_stop,), daemon=True)
        hb.start()
        try:
            while not own_stop.is_set():
                claimed = self.client.claim()
                if claimed is None:
                    idle += 1
                    if idle >= idle_rounds:
                        break
                    own_stop.wait(self.poll_interval_s)
                    continue
                idle = 0
                self.handle_one(claimed)
        finally:
            hb_stop.set()
            hb.join(timeout=1)
