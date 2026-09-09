from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from .store import Problem

_PROFILE_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
_PROFILE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_PEER_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')


def default_root() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root() / 'plugin-data' / 'bot-coms-messaging'


def peer_from_profile_name(name: str) -> str:
    """Stable spool peer from Hermes profile id — never BOT_COMS_PEER_ID (those collide across crews)."""
    raw = re.sub(r'[^a-z0-9_-]+', '-', name.strip().lower()).strip('-_')
    if not raw:
        raw = 'profile'
    if raw[0].isdigit() or raw[0] == '_':
        raw = 'p-' + raw
    peer = raw[:64]
    if not _PEER_RE.fullmatch(peer) or peer == 'inbox':
        raise Problem(503, f'Cannot derive a messaging peer from profile name {name!r}')
    return peer


def stable_profile_id(server_id: str, name: str) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, 'bot-coms-messaging:' + server_id)
    return uuid.uuid5(namespace, name).hex


def _profile_display_name(profile_dir: Path, name: str) -> str:
    path = profile_dir / 'profile.yaml'
    try:
        raw = path.read_text()
    except OSError:
        return name
    try:
        data = json.loads(raw)
    except ValueError:
        try:
            import yaml
            data = yaml.safe_load(raw)
        except Exception:
            return name
    if isinstance(data, dict):
        display = data.get('display_name')
        if isinstance(display, str) and display.strip():
            return display.strip()
    return name


def _is_deleted_named_profile(hermes_root: Path, name: str) -> bool:
    tombstone = hermes_root / 'profiles' / '.deleted' / name
    return tombstone.exists()


def discover_hermes_profiles(hermes_root: Path) -> list[tuple[str, Path]]:
    """Return (name, directory) for default + live named profiles under hermes_root."""
    found: list[tuple[str, Path]] = []
    if (hermes_root / 'config.yaml').is_file():
        found.append(('default', hermes_root))
    profiles_root = hermes_root / 'profiles'
    if profiles_root.is_dir():
        for entry in sorted(profiles_root.iterdir()):
            if not entry.is_dir() or entry.name == 'default' or entry.name.startswith('.'):
                continue
            if not _PROFILE_NAME_RE.fullmatch(entry.name):
                continue
            if _is_deleted_named_profile(hermes_root, entry.name):
                continue
            found.append((entry.name, entry))
    return found


def _validate_profile_row(p: dict, seen: set[str], peers: set[str]) -> None:
    if (not isinstance(p, dict) or not _PROFILE_ID_RE.fullmatch(p.get('id', ''))
            or p['id'] == 'user' or p['id'] in seen or not _PEER_RE.fullmatch(p.get('peer', ''))
            or p['peer'] == 'inbox' or p['peer'] in peers
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', p.get('name', ''))
            or type(p.get('enabled')) is not bool or not isinstance(p.get('display_name', p['name']), str)
            or not isinstance(p.get('principals'), list) or any(not isinstance(u, str) or not u for u in p['principals'])):
        raise Problem(503, 'Messaging profile configuration is invalid')
    seen.add(p['id'])
    peers.add(p['peer'])


def _auto_enroll_profiles(config: dict, hermes_root: Path) -> list[dict]:
    principals = config.get('default_principals')
    if not isinstance(principals, list) or not principals or any(not isinstance(u, str) or not u for u in principals):
        raise Problem(503, 'auto_enroll_profiles requires non-empty default_principals')
    explicit = config.get('profiles') or []
    if not isinstance(explicit, list):
        raise Problem(503, 'Messaging needs stable server and profile identities')
    overrides = {}
    for row in explicit:
        if isinstance(row, dict) and isinstance(row.get('name'), str) and row['name']:
            overrides[row['name']] = row
    merged: list[dict] = []
    used_names: set[str] = set()
    for name, directory in discover_hermes_profiles(hermes_root):
        used_names.add(name)
        generated = {
            'id': stable_profile_id(config['server_id'], name),
            'peer': peer_from_profile_name(name),
            'name': name,
            'display_name': _profile_display_name(directory, name),
            'enabled': True,
            'principals': list(principals),
        }
        # A live profile may be configured with a small override such as
        # {"name": "worker", "enabled": false}; retain its generated identity.
        merged.append({**generated, **overrides.get(name, {})})
    # Keep explicit rows for names that are not currently discoverable (opt-in orphans / custom).
    for name, row in overrides.items():
        if name not in used_names:
            merged.append(row)
    return merged


def load_config(root: Path) -> dict:
    # Recheck the shared instance enablement on each request and worker pass. Dashboard
    # routers are mounted at startup, so disabling a plugin must also stop existing routes.
    hermes_root = root.parent.parent
    try:
        raw = (hermes_root / 'config.yaml').read_text()
        try:
            instance = json.loads(raw)
        except ValueError:
            try:
                import yaml
                instance = yaml.safe_load(raw)
            except Exception as error:
                raise Problem(503, 'Cannot parse shared Hermes plugin enablement') from error
        plugins = instance.get('plugins', {})
        enabled, disabled = plugins.get('enabled', []), plugins.get('disabled', [])
        if not isinstance(enabled, list) or not isinstance(disabled, list) or any(
                not isinstance(item, str) for item in enabled + disabled):
            raise Problem(503, 'Hermes plugin enablement must use lists of plugin IDs')
        required = {'bot-coms', 'bot-coms-messaging'}
        if not required.issubset(enabled) or required.intersection(disabled):
            raise Problem(503, 'Messaging plugins are disabled on this Hermes instance')
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        raise Problem(503, 'Cannot verify shared Hermes plugin enablement')
    try:
        config = json.loads((root / 'config.json').read_text())
    except (OSError, ValueError):
        raise Problem(503, 'Messaging configuration is missing or invalid')
    if not isinstance(config, dict) or not isinstance(config.get('server_id'), str) or not config.get('server_id'):
        raise Problem(503, 'Messaging needs stable server and profile identities')
    if not isinstance(config.get('profiles'), list):
        raise Problem(503, 'Messaging needs stable server and profile identities')

    auto = config.get('auto_enroll_profiles') is True
    if auto:
        config = dict(config)
        config['profiles'] = _auto_enroll_profiles(config, hermes_root)

    seen, peers = set(), set()
    for p in config['profiles']:
        _validate_profile_row(p, seen, peers)
    for field, fallback, lower, upper in [
        ('max_turns', 12, 1, 100),
        ('run_timeout_seconds', 600, 5, 3600),
        ('max_mention_hops', 2, 0, 8),
        ('max_wakes_per_origin', 4, 1, 32),
    ]:
        value = config.get(field, fallback)
        if type(value) is not int or not lower <= value <= upper:
            raise Problem(503, f'Invalid {field}')
        config[field] = value
    mode = config.get('turn_taking_mode', 'on')
    if mode not in ('off', 'shadow', 'on'):
        raise Problem(503, 'Invalid turn_taking_mode')
    config['turn_taking_mode'] = mode
    return config


def allowed(config: dict, principal: str) -> list[dict]:
    return [p for p in config['profiles'] if principal in p['principals'] and p.get('enabled', False)]
