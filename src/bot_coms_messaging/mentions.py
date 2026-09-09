"""Parse @mentions from bot reply bodies for hop-capped handoffs.

User message bodies are never routed through this module — empty recipients
defer to turn-taking (or the default responder only if that call fails).

Handoffs match exact profile ids only (`@id` or `@{id}`). Name / display_name
aliases are ignored so clients can show friendly labels without affecting routing.
"""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r'```.*?```', re.DOTALL)
_INLINE_RE = re.compile(r'`[^`]+`')
# Accept @id and @{id} (models sometimes emit braces from prompt templates).
_MENTION_RE = re.compile(r'@\{([A-Za-z0-9_.-]+)\}|@([A-Za-z0-9_.-]+)')


def strip_code(body: str) -> str:
    """Remove fenced and inline code so quoted identifiers do not route."""
    return _INLINE_RE.sub(' ', _FENCE_RE.sub(' ', body or ''))


def mention_tokens(body: str) -> list[str]:
    """Return unique @tokens from body after code stripping (order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _MENTION_RE.finditer(strip_code(body)):
        token = match.group(1) or match.group(2)
        if not token:
            continue
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out


def resolve_mentions(body: str, members: list[dict]) -> list[str]:
    """Map @tokens to member profile ids.

    Matches case-insensitively against profile `id` only. Name and display_name
    are never routing aliases. Tokens may appear as @id or @{id}.
    """
    by_id: dict[str, str] = {}
    for member in members:
        pid = member.get('id')
        if isinstance(pid, str) and pid.strip():
            by_id.setdefault(pid.strip().casefold(), pid.strip())

    resolved: list[str] = []
    seen: set[str] = set()
    for token in mention_tokens(body):
        pid = by_id.get(token.casefold())
        if pid is not None and pid not in seen:
            seen.add(pid)
            resolved.append(pid)
    return resolved
