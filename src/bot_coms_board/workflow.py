"""Role-independent workflow configuration and durable assignment contracts.

JSON keeps the standalone package dependency-free. Titles and reporting lines
are directory metadata; explicit responsibility bindings select participants.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from bot_coms.envelope import validate_peer_id

ACTIVITIES = frozenset({'coordinate', 'implement', 'research', 'review'})


def load_config(team_root: Path) -> dict[str, Any]:
    path = Path(os.environ.get('BOT_COMS_WORKFLOWS', '') or team_root / 'workflows.json')
    if not path.exists():
        return {'version': 1, 'peers': {}, 'bindings': {}, 'policies': {}, 'defaults': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('workflows.json requires version 1')
    for key in ('peers', 'bindings', 'policies', 'defaults'):
        if not isinstance(data.get(key, {}), dict):
            raise ValueError(f'{key} must be an object')
        data.setdefault(key, {})
    for peer, info in data['peers'].items():
        validate_peer_id(peer)
        if not isinstance(info, dict) or not isinstance(info.get('capabilities', []), list):
            raise ValueError(f'invalid directory entry: {peer}')
        if info.get('profile'):
            validate_peer_id(info['profile'])
        if not all(isinstance(cap, str) and cap for cap in info.get('capabilities', [])):
            raise ValueError(f'invalid capabilities: {peer}')
    return data


def resolve_contract(team_root: Path, *, sender: str, worker: str,
                     activity: str, policy: str | None, parent_slice: str | None) -> dict:
    if activity not in ACTIVITIES:
        raise ValueError(f'activity must be one of {sorted(ACTIVITIES)}')
    data = load_config(team_root)
    name = policy or data['defaults'].get(activity)
    if name and name not in data['policies']:
        raise ValueError(f'unknown workflow policy: {name}')
    spec = data['policies'].get(name, {})
    if not isinstance(spec, dict):
        raise ValueError('policy must be an object')
    peers, bindings = data['peers'], data['bindings']

    def bind(responsibility: str, capability: str | None = None) -> str:
        peer = bindings.get(responsibility)
        if not isinstance(peer, str) or peer not in peers:
            raise ValueError(f'unbound responsibility: {responsibility}')
        if capability and capability not in peers[peer].get('capabilities', []):
            raise ValueError(f'{peer} lacks capability {capability}')
        return peer

    capability = spec.get('worker_capability')
    if capability and capability not in peers.get(worker, {}).get('capabilities', []):
        raise ValueError(f'{worker} lacks capability {capability}')
    owner = bind(spec['owner']) if spec.get('owner') else sender
    gates = []
    names = set()
    for gate in spec.get('gates', []):
        name_g = gate.get('id')
        if not isinstance(name_g, str) or not name_g or name_g in names:
            raise ValueError('review gate ids must be unique nonempty strings')
        names.add(name_g)
        reviewer = bind(gate['responsibility'], gate.get('capability'))
        independent = gate.get('independent', True)
        if not isinstance(independent, bool):
            raise ValueError('independent must be boolean')
        if independent and reviewer == worker:
            raise ValueError(f'{name_g} requires an independent reviewer')
        gates.append({'id': name_g, 'peer': reviewer, 'independent': independent, 'capability': gate.get('capability')})
    return {'version': 1, 'return_peer': sender, 'owner_peer': owner,
            'parent_slice': parent_slice, 'activity': activity,
            'policy': name, 'gates': gates, 'worker_capability': capability,
            'generation': 1, 'revision': 0, 'submission': None}
