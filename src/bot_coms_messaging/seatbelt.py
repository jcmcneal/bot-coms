"""Work-aware heartbeat watchdog for messaging admission.

Arms only while durable work is pending. No permanent poll metronome.
"""
from __future__ import annotations

import asyncio
import logging

from bot_coms import dashboard_wake

from .store import Store

logger = logging.getLogger(__name__)

DEFAULT_STALE_SECONDS = 30.0
DEFAULT_CHECK_INTERVAL = 5.0


class MessagingSeatbelt:
    """Poke the dashboard wake host when heartbeat goes stale with work pending."""

    def __init__(
        self,
        store: Store,
        *,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
        check_interval: float = DEFAULT_CHECK_INTERVAL,
    ):
        self.store = store
        self.stale_seconds = stale_seconds
        self.check_interval = check_interval
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def sync(self) -> None:
        """Arm while work is pending; disarm when idle."""
        if self.store.has_pending_work():
            self._arm()
        else:
            self._disarm()

    def _arm(self) -> None:
        if self._task is not None and not self._stopping.is_set():
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name='bot-coms-messaging-seatbelt')

    def _disarm(self) -> None:
        if self._task is None:
            return
        self._stopping.set()

    async def stop(self) -> None:
        self._disarm()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.check_interval)
                    break
                except asyncio.TimeoutError:
                    pass
                if self._stopping.is_set():
                    break
                if not self.store.has_pending_work():
                    break
                age = self.store.heartbeat_age()
                if age is not None and age > self.stale_seconds:
                    logger.warning(
                        'messaging heartbeat stale (%.1fs) with pending work; poking wake host',
                        age,
                    )
                    dashboard_wake.poke()
        finally:
            self._task = None
