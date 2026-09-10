"""One wake-driven loop for team delivery and messaging admission."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from bot_coms import dashboard_wake

logger = logging.getLogger(__name__)

Handler = Callable[[], Awaitable[None]]


class WakeHost:
    def __init__(self):
        self._handlers: list[Handler] = []
        self._stopping = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._doorbell_roots: set[Path] = set()

    @property
    def active(self) -> bool:
        return self._task is not None and not self._stopping.is_set()

    def install_doorbell(self, hermes_root: Path) -> None:
        """Register the dashboard wake runner on the shared doorbell path."""
        from bot_coms.doorbell import _default_wake, set_wake_runner

        root = Path(hermes_root).expanduser().resolve()
        if root in self._doorbell_roots:
            return
        team = root / 'team'
        spool = team / 'spool'

        def runner(profile: str, peer_id: str, env) -> None:
            _default_wake(profile, peer_id, env, team=team, spool_root=spool)

        set_wake_runner(runner)
        self._doorbell_roots.add(root)
        dashboard_wake.bind(asyncio.get_running_loop(), team_root=team)

    def register(self, handler: Handler) -> None:
        if handler in self._handlers:
            return
        self._handlers.append(handler)
        if self._task is None:
            self._stopping = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name='bot-coms-dashboard-wake-host')

    async def unregister(self, handler: Handler) -> None:
        if handler not in self._handlers:
            return
        self._handlers.remove(handler)
        if not self._handlers and self._task is not None:
            self._stopping.set()
            await self._task
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                await dashboard_wake.wait(self._stopping)
                if self._stopping.is_set():
                    break
                for handler in list(self._handlers):
                    try:
                        await handler()
                    except Exception:
                        logger.exception('dashboard wake handler failed')
        finally:
            self._task = None


_host: WakeHost | None = None


def host() -> WakeHost:
    global _host
    if _host is None:
        _host = WakeHost()
    return _host


async def shutdown_host() -> None:
    global _host
    if _host is None:
        return
    _host._handlers.clear()
    if _host._task is not None:
        _host._stopping.set()
        await _host._task
    from bot_coms.doorbell import set_wake_runner

    set_wake_runner(None)
    _host._doorbell_roots.clear()
    dashboard_wake.close()
    _host = None
