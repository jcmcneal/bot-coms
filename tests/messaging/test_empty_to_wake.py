"""Empty-To wake poke and cross-process team FIFO bind."""
import asyncio
import json
import threading
import time

import pytest

from bot_coms import dashboard_wake
from bot_coms_messaging.service import MessagingService
from bot_coms_runtime.wake_host import host, shutdown_host
from tests.messaging.test_messaging import root
from tests.messaging.test_service import Runtime, StubSelector, set_mode


@pytest.fixture(autouse=True)
def reset_wake_state():
    asyncio.run(shutdown_host())
    dashboard_wake.close()
    yield
    asyncio.run(shutdown_host())
    dashboard_wake.close()


def _group_send(store, body='anyone?'):
    group = store.groups('test:alice', 'Bruv', ['swe-id', 'designer-id'], 'swe-id', 'g-empty-to')
    return store.send('test:alice', 'empty-to', body, [], cid=group['id'])


def test_empty_to_send_pokes_wake_host_without_manual_tick(root):
    set_mode(root, 'on')
    selector = StubSelector({'action': 'yield', 'reason': 'human'})
    runtime = Runtime()

    async def runner():
        backend = MessagingService(root, runtime, selector=selector)
        await backend.start()
        try:
            _group_send(backend.store)
            deadline = time.monotonic() + 2
            yielded = []
            while time.monotonic() < deadline:
                events = backend.store.events('test:alice', 0)['events']
                yielded = [e for e in events if e['kind'] == 'turn.yielded']
                if yielded:
                    break
                await asyncio.sleep(0.02)
            assert len(yielded) == 1
            assert json.loads(yielded[0]['detail'])['reason'] == 'human'
            assert runtime.calls == []
        finally:
            await backend.stop()

    asyncio.run(runner())


def test_cross_process_poke_reaches_messaging_bind(root):
    team = root.parent.parent / 'team'
    poked = asyncio.Event()

    async def runner():
        dashboard_wake.bind(asyncio.get_running_loop(), team_root=team)
        wake = host()

        async def on_wake():
            poked.set()

        wake.register(on_wake)
        await asyncio.sleep(0)
        pipe = team / '.dashboard-wake.pipe'
        assert pipe.exists()

        def writer():
            with pipe.open('wb') as handle:
                handle.write(b'\0')

        threading.Thread(target=writer, daemon=True).start()
        await asyncio.wait_for(poked.wait(), timeout=2)
        await wake.unregister(on_wake)

    asyncio.run(runner())
