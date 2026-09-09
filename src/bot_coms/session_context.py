"""Task-local Hermes tool configuration, with CLI environment fallback."""
from __future__ import annotations

import os


def get_env(key: str, default: str | None = None) -> str | None:
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return os.environ.get(key, default)
    value = get_session_env(key, None)
    return value if value is not None else os.environ.get(key, default)
