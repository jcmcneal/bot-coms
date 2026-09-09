"""Parse @mentions from bot reply bodies for hop-capped handoffs.

User message bodies are never routed through this module — empty recipients
defer to turn-taking (or the default responder only if that call fails).
"""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r'```.*?```', re.DOTALL)
_INLINE_RE = re.compile(r'`[^`]+`')
_MENTION_RE = re.compile(r'@([A-Za-z0-9_.-]+)')


def strip_code(body: str) -> str:
    """Remove fenced and inline code so quoted identifiers do not route."""
    return _INLINE_RE.sub(' ', _FENCE_RE.sub(' ', body or ''))


def mention_tokens(body: str) -> list[str]:
    """Return unique @tokens from body after code stripping (order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _MENTION_RE.finditer(strip_code(body)):
        token = match.group(1)
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out


def resolve_mentions(body: str, members: list[dict]) -> list[str]:
    """Map @tokens to member profile ids.

    Matches case-insensitively against id, name, and display_name. When multiple
    members could match the same token, the longest alias wins (then id order).
    """
    aliases: list[tuple[str, str, int]] = []  # (fold, profile_id, length)
    for member in members:
        pid = member.get('id')
        if not isinstance(pid, str) or not pid:
            continue
        for field in ('id', 'name', 'display_name', 'displayName'):
            value = member.get(field)
            if isinstance(value, str) and value.strip():
                aliases.append((value.strip().casefold(), pid, len(value.strip())))
    aliases.sort(key=lambda row: (-row[2], row[1]))

    resolved: list[str] = []
    seen: set[str] = set()
    for token in mention_tokens(body):
        fold = token.casefold()
        for alias, pid, _ in aliases:
            if alias == fold and pid not in seen:
                seen.add(pid)
                resolved.append(pid)
                break
    return resolved
