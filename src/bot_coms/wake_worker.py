"""Compatibility guard for retired standalone peer wake entry points.

Pending wake-state files are imported by TeamRuntime under the Hermes backend.
Calling this old module can never spawn a supervisor or a fresh CLI session.
"""
from __future__ import annotations

from pathlib import Path


def start(directory: Path, profile: str, hermes: str) -> None:
    raise RuntimeError('standalone wake workers are retired; enable the Hermes bot-coms backend runtime')


def recover(team: Path) -> None:
    """Retained for old callers; backend startup owns durable recovery."""
    return None


def drain(directory: Path, profile: str, hermes: str) -> None:
    start(directory, profile, hermes)


if __name__ == '__main__':
    raise SystemExit('standalone wake workers are retired; pending work belongs to the Hermes backend')
