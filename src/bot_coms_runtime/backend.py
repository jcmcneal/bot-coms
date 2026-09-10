"""Run durable team delivery inside the existing Hermes dashboard process.

The queue remains authoritative. The short scan interval covers deliveries from
other processes (including coding-agent EXIT callbacks), with no sidecar.
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
    def __init__(self, root: Path, *, factory=create_runtime, interval: float = 1.0):
        if interval <= 0:
            raise ValueError('interval must be positive')
        self.root = Path(root)
        self.factory, self.interval = factory, interval
        self.runtime = None
        self.task = None
        self.stopping = asyncio.Event()
        self.last_error = None
        self.last_result = None
        self._attention = None

    async def tick(self):
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

    async def _run(self):
        try:
            while not self.stopping.is_set():
                try:
                    await self.tick()
                    self.last_error = None
                except Exception as exc:
                    # Fail this service independently; never block unrelated dashboard features.
                    if type(exc).__name__ != self.last_error:
                        logger.exception('bot-coms backend delivery is unavailable')
                    self.last_error = type(exc).__name__
                try:
                    await asyncio.wait_for(self.stopping.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self.runtime is not None:
                self.runtime.close()
                self.runtime = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name='bot-coms-team-delivery')

    async def stop(self):
        self.stopping.set()
        if self.task is not None:
            await self.task
            self.task = None


@asynccontextmanager
async def backend_lifespan(root: Path, *, factory=create_runtime, interval: float = 1.0):
    backend = TeamBackend(root, factory=factory, interval=interval)
    backend.start()
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
