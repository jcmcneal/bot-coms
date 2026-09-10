"""Thread-safe mtime-busted cache for Hermes JSON/YAML config files.

Dashboard ticks recheck plugin enablement often. Parse once per path+mtime so
disable/re-enable still works when the file changes, without a TTL.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
# resolved path str -> (mtime_ns, parsed value)
_cache: dict[str, tuple[int, Any]] = {}
# Test counter: increments on each filesystem read (miss / bust).
_reads = 0


def clear_cache() -> None:
    """Drop all cached entries (tests / rare force-refresh)."""
    global _reads
    with _lock:
        _cache.clear()
        _reads = 0


def cache_stats() -> dict[str, int]:
    with _lock:
        return {'entries': len(_cache), 'reads': _reads}


def _parse_json_or_yaml(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        import yaml
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError as error:
            raise ValueError(str(error)) from error


def load_json_or_yaml(path: Path) -> Any:
    """Return parsed JSON-or-YAML contents, reusing the last parse for this mtime.

    Raises the same OSError / yaml errors callers already handle. Does not TTL.
    """
    global _reads
    path = Path(path)
    key = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        with _lock:
            _cache.pop(key, None)
        raise

    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == mtime_ns:
            return _copy_cached(hit[1])

    raw = path.read_text()
    parsed = _parse_json_or_yaml(raw)

    with _lock:
        _reads += 1
        # Another writer may have raced; re-stat before publishing.
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            _cache.pop(key, None)
            raise
        _cache[key] = (mtime_ns, parsed)
        return _copy_cached(parsed)


def load_json(path: Path) -> Any:
    """Return parsed JSON contents, reusing the last parse for this mtime."""
    global _reads
    path = Path(path)
    key = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        with _lock:
            _cache.pop(key, None)
        raise

    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == mtime_ns:
            return _copy_cached(hit[1])

    raw = path.read_text()
    parsed = json.loads(raw)

    with _lock:
        _reads += 1
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            _cache.pop(key, None)
            raise
        _cache[key] = (mtime_ns, parsed)
        return _copy_cached(parsed)


def _copy_cached(value: Any) -> Any:
    """Shallow-copy mappings/lists so callers cannot poison the cache."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value
