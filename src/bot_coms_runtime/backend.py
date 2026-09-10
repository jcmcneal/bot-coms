"""Run durable team delivery inside the existing Hermes dashboard process.

The queue remains authoritative. Work wakes the host through ``doorbell`` →
``enqueue_wake`` → ``dashboard_wake.poke``; there is no poll metronome.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


def configured(root: Path) -> bool:
    """Check shared enablement on every pass, including after plugin disable."""
    try:
        from bot_coms.hermes_config_cache import load_json_or_yaml
        config = load_json_or_yaml(root / 'config.yaml')
        plugins = config.get('plugins', {})
        enabled, disabled = plugins.get('enabled', []), plugins.get('disabled', [])
        if not all(isinstance(values, list) and all(isinstance(item, str) for item in values)
                   for values in (enabled, disabled)):
            return False
        return ('bot-coms' in enabled and 'bot-coms' not in disabled
                and ((root / 'team' / 'workflows.json').is_file()
                     or (root / 'team' / 'spool').is_dir()))
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        return False



def default_root() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root()


def create_runtime(root: Path):
    from bot_coms.team_runtime import TeamRuntime
    from bot_coms_board.coordinator import reconcile_team
    from bot_coms_runtime.cli_sessions import CliSessionRuntime
    return TeamRuntime(team_root=root / 'team', spool_root=root / 'team' / 'spool',
                       executor=CliSessionRuntime(root, 'bot-coms'),
                       reconciler=lambda: reconcile_team(root / 'team', root / 'team' / 'spool'))


class TeamBackend:
    """One tracked lifecycle, with a single tick in flight at a time."""
    def __init__(self, root: Path, *, factory=create_runtime):
        self.root = Path(root)
        self.factory = factory
        self.runtime = None
        self.last_error = None
        self.last_result = None
        self._attention = None
        self._registered = False

    async def tick(self):
        try:
            if not configured(self.root):
                if self.runtime is not None:
                    self.runtime.close(cancel=True)
                    self.runtime = None
                return
            if self.runtime is None:
                runtime = self.factory(self.root)
                try:
                    runtime.start()
                except BaseException:
                    runtime.close()
                    raise
                self.runtime = runtime
            self.last_result = await self.runtime.tick()
            if isinstance(self.last_result, dict):
                attention = self.last_result.get('attention', [])
                errors = self.last_result.get('errors', [])
                signature = json.dumps([attention, errors], sort_keys=True)
                if (attention or errors) and signature != self._attention:
                    logger.warning('bot-coms team delivery requires attention: %s', signature)
                self._attention = signature
            self.last_error = None
        except Exception as exc:
            # Fail this service independently; never block unrelated dashboard features.
            if type(exc).__name__ != self.last_error:
                logger.exception('bot-coms backend delivery is unavailable')
            self.last_error = type(exc).__name__

    async def start(self):
        from bot_coms import dashboard_wake
        from bot_coms_runtime.wake_host import host

        loop = asyncio.get_running_loop()
        dashboard_wake.bind(loop, team_root=self.root / 'team')
        wake = host()
        wake.install_doorbell(self.root)
        await self.tick()
        if not self._registered:
            wake.register(self.tick)
            self._registered = True

    async def stop(self):
        from bot_coms_runtime.wake_host import host

        if self._registered:
            await host().unregister(self.tick)
            self._registered = False
        if self.runtime is not None:
            self.runtime.close()
            self.runtime = None


@asynccontextmanager
async def backend_lifespan(root: Path, *, factory=create_runtime):
    backend = TeamBackend(root, factory=factory)
    await backend.start()
    try:
        yield backend
    finally:
        await backend.stop()


def create_router(root_factory=default_root, *, factory=create_runtime):
    # FastAPI is a backend extra, so transport-only users keep stdlib imports.
    from fastapi import APIRouter

    @asynccontextmanager
    async def lifespan(app):
        async with backend_lifespan(root_factory(), factory=factory):
            yield

    return APIRouter(lifespan=lifespan)
