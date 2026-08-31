"""Atomic JSON write: tmp file, fsync, rename."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bot_coms.envelope import generate_ulid
from bot_coms.permissions import assert_under_root, chmod_file

# Tests inject a callback that raises at named stages (e.g. "before_rename").
_FAULT: Callable[[str], None] | None = None


def set_fault_hook(hook: Callable[[str], None] | None) -> None:
    global _FAULT
    _FAULT = hook


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fault(stage: str) -> None:
    hook = _FAULT
    if hook is not None:
        hook(stage)


def write_json_atomic(
    dest_dir: Path,
    name: str,
    obj: Any,
    *,
    tmp_dir: Path,
    root: Path,
    file_mode: int = 0o600,
) -> Path:
    assert_under_root(dest_dir, root)
    assert_under_root(tmp_dir, root)
    dest = dest_dir / name
    assert_under_root(dest, root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    partial = tmp_dir / f"{generate_ulid()}.{os.getpid()}.{secrets.token_hex(4)}.partial"
    assert_under_root(partial, root)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fd = -1
    try:
        fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_EXCL, file_mode)
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
        os.close(fd)
        fd = -1
        chmod_file(partial, file_mode)
        _fsync_dir(tmp_dir)
        _fault("before_rename")
        os.replace(str(partial), str(dest))
        _fsync_dir(dest_dir)
        return dest
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def append_jsonl_line(path: Path, obj: Any, *, file_mode: int = 0o600) -> None:
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(str(path), flags, file_mode)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    chmod_file(path, file_mode)
