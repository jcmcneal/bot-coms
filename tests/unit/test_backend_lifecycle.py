"""The optional dashboard backend owns team execution independently of messaging."""
import asyncio
import json

import pytest

from bot_coms_runtime.backend import TeamBackend, create_router
from bot_coms_runtime.wake_host import shutdown_host


def configure(root, enabled=True):
    (root / 'config.yaml').write_text(json.dumps({
        'plugins': {'enabled': ['bot-coms'] if enabled else []}}))
    (root / 'team' / 'spool').mkdir(parents=True, exist_ok=True)


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


@pytest.fixture(autouse=True)
def reset_wake_host():
    from bot_coms.dashboard_wake import close

    asyncio.run(shutdown_host())
    close()
    yield
    asyncio.run(shutdown_host())
    close()


def test_team_runs_without_messaging_and_stops_on_disable(tmp_path, monkeypatch):
    monkeypatch.setenv('BOT_COMS_DOORBELL', '1')
    configure(tmp_path)
    runtime = Runtime()
    backend = TeamBackend(tmp_path, factory=lambda _: runtime)

    async def exercise():
        await backend.start()
        assert (runtime.starts, runtime.ticks) == (1, 1)
        configure(tmp_path, enabled=False)
        from bot_coms.dashboard_wake import poke
        poke()
        await asyncio.sleep(0.05)
        assert runtime.closes == 1
        assert runtime.cancelled
        assert runtime.ticks == 1
        await backend.stop()
    asyncio.run(exercise())


def test_router_lifespan_is_inert_until_backend_starts(tmp_path):
    pytest.importorskip('fastapi')
    pytest.importorskip('httpx')
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    configure(tmp_path)
    runtime = Runtime()
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, factory=lambda _: runtime))

    @app.get('/probe')
    async def probe():
        return {'ticks': runtime.ticks}

    assert runtime.starts == 0
    with TestClient(app) as client:
        assert client.get('/probe').json()['ticks'] >= 1
        assert runtime.starts == 1
    assert runtime.closes == 1


def test_missing_backend_service_does_not_break_dashboard(tmp_path):
    configure(tmp_path)
    def unavailable(_):
        raise RuntimeError('Hermes session service unavailable')
    backend = TeamBackend(tmp_path, factory=unavailable)

    async def exercise():
        await backend.start()
        assert backend.last_error == 'RuntimeError'
        await backend.stop()
    asyncio.run(exercise())


@pytest.mark.parametrize('invalid', [
    'plugins: [',
    '{"plugins":{"enabled":"bot-coms"}}',
    '{"plugins":{"enabled":["bot-coms"],"disabled":"other"}}',
])
def test_invalid_config_cancels_active_backend(tmp_path, invalid, monkeypatch):
    monkeypatch.setenv('BOT_COMS_DOORBELL', '1')
    configure(tmp_path)
    runtime = Runtime()
    backend = TeamBackend(tmp_path, factory=lambda _: runtime)
    async def exercise():
        await backend.start()
        (tmp_path / 'config.yaml').write_text(invalid)
        from bot_coms.dashboard_wake import poke
        poke()
        await asyncio.sleep(0.05)
        assert runtime.cancelled
        assert backend.runtime is None
        await backend.stop()
    asyncio.run(exercise())
