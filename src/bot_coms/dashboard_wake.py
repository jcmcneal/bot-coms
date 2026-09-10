"""In-process and cross-process wake signals for dashboard backends.

Durable work still lands through ``doorbell`` → ``enqueue_wake``.  This module
only unblocks the asyncio host so it can admit/reconcile once per poke — not a
second scheduler and not FSEvents/kqueue/watchdog.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

_wake_event: asyncio.Event | None = None
_wake_pipe: Path | None = None
_pipe_fd: int | None = None
_loop: asyncio.AbstractEventLoop | None = None


def bind(loop: asyncio.AbstractEventLoop, *, team_root: Path | None = None) -> None:
    """Attach wake waiters to the running dashboard event loop."""
    global _wake_event, _wake_pipe, _pipe_fd, _loop
    _loop = loop
    if _wake_event is None:
        _wake_event = asyncio.Event()
    if team_root is None:
        return
    team_root = Path(team_root).expanduser().resolve()
    path = team_root / '.dashboard-wake.pipe'
    if _wake_pipe == path and _pipe_fd is not None:
        return
    _wake_pipe = path
    team_root.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        os.mkfifo(path, 0o600)
    else:
        path.chmod(0o600)
    if _pipe_fd is not None:
        loop.remove_reader(_pipe_fd)
        os.close(_pipe_fd)
        _pipe_fd = None
    fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    _pipe_fd = fd
    loop.add_reader(fd, _on_pipe_readable, fd)


def _signal_event() -> None:
    if _wake_event is None or _loop is None:
        return
    try:
        if asyncio.get_running_loop() is _loop:
            _wake_event.set()
            return
    except RuntimeError:
        pass
    if _loop.is_running():
        _loop.call_soon_threadsafe(_wake_event.set)


def _on_pipe_readable(fd: int) -> None:
    try:
        while os.read(fd, 4096):
            pass
    except BlockingIOError:
        pass
    _signal_event()


def poke() -> None:
    """Wake sleeping dashboard handlers once. Safe from threads and other processes."""
    if _wake_event is not None and _loop is not None:
        _signal_event()
        return
    if _wake_pipe is None:
        return
    try:
        with _wake_pipe.open('wb') as handle:
            handle.write(b'\0')
    except OSError:
        return


async def wait(stopping: asyncio.Event) -> None:
    """Block until ``poke`` or ``stopping``."""
    if _wake_event is None:
        await stopping.wait()
        return
    if _wake_event.is_set():
        _wake_event.clear()
        return
    wake = asyncio.create_task(_wake_event.wait(), name='bot-coms-dashboard-wake')
    stop = asyncio.create_task(stopping.wait(), name='bot-coms-dashboard-stop')
    done, pending = await asyncio.wait({wake, stop}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if wake in done:
        _wake_event.clear()


def close() -> None:
    """Drop pipe reader state (tests and shutdown)."""
    global _wake_event, _wake_pipe, _pipe_fd, _loop
    if _loop is not None and _pipe_fd is not None:
        _loop.remove_reader(_pipe_fd)
        os.close(_pipe_fd)
    _wake_event = None
    _wake_pipe = None
    _pipe_fd = None
    _loop = None
