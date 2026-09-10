"""mtime-busted Hermes config cache for dashboard tick hot paths."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from bot_coms.hermes_config_cache import cache_stats, clear_cache, load_json, load_json_or_yaml
from bot_coms.team_runtime import profile_authorized
from bot_coms_runtime.backend import configured


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _write(path: Path, payload, *, bump_mtime: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and bump_mtime:
        # Ensure mtime_ns advances even on coarse filesystems / same-second writes.
        prior = path.stat().st_mtime_ns
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        now = time.time() + 1.1
        os.utime(path, (now, now))
        assert path.stat().st_mtime_ns != prior
    else:
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def test_load_json_or_yaml_cache_hit_skips_reread(tmp_path, monkeypatch):
    path = _write(tmp_path / 'config.yaml', {'plugins': {'enabled': ['bot-coms']}})
    assert load_json_or_yaml(path)['plugins']['enabled'] == ['bot-coms']
    assert cache_stats()['reads'] == 1

    calls = {'n': 0}
    real = Path.read_text

    def counted(self, *args, **kwargs):
        if self == path:
            calls['n'] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', counted)
    again = load_json_or_yaml(path)
    assert again['plugins']['enabled'] == ['bot-coms']
    assert calls['n'] == 0
    assert cache_stats()['reads'] == 1


def test_load_json_or_yaml_mtime_bust_reloads(tmp_path):
    path = _write(tmp_path / 'config.yaml', {'plugins': {'enabled': ['bot-coms']}})
    assert load_json_or_yaml(path)['plugins']['enabled'] == ['bot-coms']
    _write(path, {'plugins': {'enabled': []}}, bump_mtime=True)
    assert load_json_or_yaml(path)['plugins']['enabled'] == []
    assert cache_stats()['reads'] == 2


def test_configured_disable_enable_respects_mtime(tmp_path):
    spool = tmp_path / 'team' / 'spool'
    spool.mkdir(parents=True)
    config = tmp_path / 'config.yaml'
    _write(config, {'plugins': {'enabled': ['bot-coms']}})
    assert configured(tmp_path) is True
    assert configured(tmp_path) is True  # hit
    assert cache_stats()['reads'] == 1

    _write(config, {'plugins': {'enabled': []}}, bump_mtime=True)
    assert configured(tmp_path) is False

    _write(config, {'plugins': {'enabled': ['bot-coms']}}, bump_mtime=True)
    assert configured(tmp_path) is True
    assert cache_stats()['reads'] == 3


def test_profile_authorized_disable_enable_respects_mtime(tmp_path):
    home = tmp_path / 'profiles' / 'worker'
    config = home / 'config.yaml'
    _write(config, {'plugins': {'enabled': ['bot-coms', 'bot-coms-board']}})
    team = tmp_path / 'team'
    team.mkdir()
    assert profile_authorized(team, 'worker', board=True) is True
    assert profile_authorized(team, 'worker', board=True) is True
    assert cache_stats()['reads'] == 1

    _write(config, {'plugins': {'enabled': ['bot-coms'], 'disabled': ['bot-coms-board']}}, bump_mtime=True)
    assert profile_authorized(team, 'worker', board=True) is False
    assert profile_authorized(team, 'worker', board=False) is True

    _write(config, {'plugins': {'enabled': ['bot-coms', 'bot-coms-board']}}, bump_mtime=True)
    assert profile_authorized(team, 'worker', board=True) is True


def test_messaging_load_config_cache_hit_and_disable_mtime(tmp_path):
    from bot_coms_messaging.config import Problem, load_config

    hermes = tmp_path
    messaging = hermes / 'plugin-data' / 'bot-coms-messaging'
    messaging.mkdir(parents=True)
    instance = hermes / 'config.yaml'
    _write(instance, {'plugins': {'enabled': ['bot-coms', 'bot-coms-messaging']}})
    _write(messaging / 'config.json', {
        'server_id': 'srv',
        'profiles': [
            dict(id='swe-id', peer='swe', name='swe', display_name='SWE',
                 enabled=True, principals=['test:alice']),
        ],
    })

    first = load_config(messaging)
    second = load_config(messaging)
    assert first['server_id'] == second['server_id'] == 'srv'
    # One read for config.yaml + one for config.json on the first call; hits after.
    assert cache_stats()['reads'] == 2

    _write(instance, {'plugins': {'enabled': ['bot-coms']}}, bump_mtime=True)
    with pytest.raises(Problem) as err:
        load_config(messaging)
    assert 'disabled' in err.value.detail.lower() or 'Messaging plugins' in err.value.detail

    _write(instance, {'plugins': {'enabled': ['bot-coms', 'bot-coms-messaging']}}, bump_mtime=True)
    assert load_config(messaging)['server_id'] == 'srv'


def test_messaging_config_json_mtime_bust(tmp_path):
    from bot_coms_messaging.config import load_config

    hermes = tmp_path
    messaging = hermes / 'plugin-data' / 'bot-coms-messaging'
    messaging.mkdir(parents=True)
    _write(hermes / 'config.yaml', {'plugins': {'enabled': ['bot-coms', 'bot-coms-messaging']}})
    cfg = messaging / 'config.json'
    _write(cfg, {
        'server_id': 'srv',
        'max_turns': 12,
        'profiles': [
            dict(id='swe-id', peer='swe', name='swe', display_name='SWE',
                 enabled=True, principals=['test:alice']),
        ],
    })
    assert load_config(messaging)['max_turns'] == 12
    _write(cfg, {
        'server_id': 'srv',
        'max_turns': 8,
        'profiles': [
            dict(id='swe-id', peer='swe', name='swe', display_name='SWE',
                 enabled=True, principals=['test:alice']),
        ],
    }, bump_mtime=True)
    assert load_config(messaging)['max_turns'] == 8


def test_load_json_cache_hit(tmp_path, monkeypatch):
    path = _write(tmp_path / 'config.json', {'server_id': 'x'})
    assert load_json(path)['server_id'] == 'x'
    calls = {'n': 0}
    real = Path.read_text

    def counted(self, *args, **kwargs):
        if self == path:
            calls['n'] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', counted)
    assert load_json(path)['server_id'] == 'x'
    assert calls['n'] == 0
