"""Doorbell-driven dashboard wake: one admit per enqueue, idle without polling."""
import asyncio
import json
import time
from contextlib import closing
import pytest

from bot_coms import Client, init_spool
from bot_coms.dashboard_wake import bind, close, poke, wait
from bot_coms.team_runtime import TeamRuntime
from bot_coms_runtime.backend import TeamBackend, configured
from bot_coms_runtime.wake_host import host, shutdown_host


class Native:
    def __init__(self):
        self.calls = []
        self.receipts = {}

    def status(self, *, principal_id, operation_key):
        return self.receipts.get((principal_id, operation_key))

    def submit(self, **args):
        self.calls.append(args)
        key = args['principal_id'], args['operation_key']
        self.receipts[key] = dict(status='running', session_id='session-1', error=None)
        return self.receipts[key]

    def cancel(self, *, principal_id, operation_key):
        return self.receipts.get((principal_id, operation_key))


class Runtime:
    def __init__(self):
        self.starts = self.ticks = self.closes = 0
        self.cancelled = False

    def start(self):
        self.starts += 1

    async def tick(self):
        self.ticks += 1

    def close(self, *, cancel=False):
        self.closes += 1
        self.cancelled = self.cancelled or cancel


def configure_team(root, enabled=True):
    (root / 'config.yaml').write_text(json.dumps({
        'plugins': {'enabled': ['bot-coms'] if enabled else []}}))
    (root / 'team' / 'spool').mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def reset_wake_state():
    asyncio.run(shutdown_host())
    close()
    yield
    asyncio.run(shutdown_host())
    close()


def test_enqueue_wake_pokes_dashboard_once(tmp_path, monkeypatch):
    team = tmp_path / 'team'
    init_spool(team / 'spool', ['sender', 'worker'])
    for peer in ('sender', 'worker'):
        home = tmp_path / 'profiles' / peer
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['bot-coms', 'bot-coms-board']}}))
    monkeypatch.setenv('BOT_COMS_DOORBELL', '1')
    monkeypatch.setenv('BOT_COMS_TEAM_ROOT', str(team))
    monkeypatch.setenv('BOT_COMS_SPOOL_ROOT', str(team / 'spool'))
    native = Native()
    runtime = TeamRuntime(team_root=team, executor=native)

    async def exercise():
        bind(asyncio.get_running_loop(), team_root=team)
        wake = host()
        wake.install_doorbell(tmp_path)
        ticks = 0

        async def on_wake():
            nonlocal ticks
            ticks += 1
            await runtime.tick()

        wake.register(on_wake)
        await asyncio.sleep(0)
        with closing(Client(team / 'spool', 'sender')) as sender:
            sender.send('worker', 'event', {'schema_version': '1.0', 'intent': 'assign', 'slice': 'S1'})
        deadline = time.monotonic() + 1
        while ticks < 1 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert ticks == 1
        assert len(native.calls) == 1
        poke()
        await asyncio.sleep(0.05)
        assert len(native.calls) == 1
        await wake.unregister(on_wake)
        runtime.close()

    asyncio.run(exercise())


def test_idle_backend_has_no_poll_interval(tmp_path):
    configure_team(tmp_path)
    backend = TeamBackend(tmp_path, factory=lambda _: Runtime())
    assert not hasattr(backend, 'interval')


def test_disable_plugin_stops_team_work(tmp_path, monkeypatch):
    monkeypatch.setenv('BOT_COMS_DOORBELL', '1')
    configure_team(tmp_path)
    runtime = Runtime()
    backend = TeamBackend(tmp_path, factory=lambda _: runtime)

    async def exercise():
        await backend.start()
        assert runtime.ticks == 1
        configure_team(tmp_path, enabled=False)
        poke()
        await asyncio.sleep(0.05)
        assert runtime.ticks == 1
        assert runtime.cancelled
        assert runtime.closes == 1
        await backend.stop()

    asyncio.run(exercise())


def test_wait_returns_on_poke():
    stopping = asyncio.Event()

    async def exercise():
        bind(asyncio.get_running_loop())
        waiter = asyncio.create_task(wait(stopping))
        await asyncio.sleep(0.01)
        assert not waiter.done()
        poke()
        await asyncio.wait_for(waiter, timeout=1)
        stopping.set()
        await wait(stopping)

    asyncio.run(exercise())
