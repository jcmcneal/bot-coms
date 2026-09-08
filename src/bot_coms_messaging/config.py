from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .store import Problem


def default_root() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root() / 'plugin-data' / 'bot-coms-messaging'


def load_config(root: Path) -> dict:
    # Recheck the shared instance enablement on each request and worker pass. Dashboard
    # routers are mounted at startup, so disabling a plugin must also stop existing routes.
    try:
        raw = (root.parent.parent / 'config.yaml').read_text()
        try:
            instance = json.loads(raw)
        except ValueError:
            import yaml
            instance = yaml.safe_load(raw)
        plugins = instance.get('plugins', {})
        required = {'bot-coms', 'bot-coms-messaging'}
        if not required.issubset((plugins.get('enabled') or [])) or required.intersection((plugins.get('disabled') or [])):
            raise Problem(503, 'Messaging plugins are disabled on this Hermes instance')
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        raise Problem(503, 'Cannot verify shared Hermes plugin enablement')
    try:
        config = json.loads((root / 'config.json').read_text())
    except (FileNotFoundError, ValueError):
        raise Problem(503, 'Messaging configuration is missing or invalid')
    if not isinstance(config, dict) or not isinstance(config.get('server_id'), str) or not config.get('server_id') or not isinstance(config.get('profiles'), list):
        raise Problem(503, 'Messaging needs stable server and profile identities')
    seen, peers = set(), set()
    for p in config['profiles']:
        if (not isinstance(p, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', p.get('id', ''))
                or p['id'] == 'user' or p['id'] in seen or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', p.get('peer', ''))
                or p['peer'] == 'inbox' or p['peer'] in peers
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', p.get('name', ''))
                or type(p.get('enabled')) is not bool or not isinstance(p.get('display_name', p['name']), str)
                or not isinstance(p.get('principals'), list) or any(not isinstance(u, str) or not u for u in p['principals'])):
            raise Problem(503, 'Messaging profile configuration is invalid')
        seen.add(p['id']); peers.add(p['peer'])
    for field, fallback, lower, upper in [('max_turns', 12, 1, 100), ('run_timeout_seconds', 600, 5, 3600)]:
        value = config.get(field, fallback)
        if type(value) is not int or not lower <= value <= upper:
            raise Problem(503, f'Invalid {field}')
    if not isinstance(config.get('hermes_executable'), str):
        raise Problem(503, 'Configure an absolute Hermes executable path')
    executable = Path(config['hermes_executable'])
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise Problem(503, 'Configure an absolute Hermes executable path')
    return config


def allowed(config: dict, principal: str) -> list[dict]:
    return [p for p in config['profiles'] if principal in p['principals'] and p.get('enabled', False)]
