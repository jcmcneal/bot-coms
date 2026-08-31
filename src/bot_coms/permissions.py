"""Mode policy, realpath jail, and allowlist token checks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any

from bot_coms.types import PermissionDenied


def assert_under_root(path: Path, root: Path) -> Path:
    real = path.resolve()
    real_root = root.resolve()
    try:
        real.relative_to(real_root)
    except ValueError as exc:
        raise PermissionDenied(f"path escapes root: {path}") from exc
    return real


def ensure_dir(path: Path, *, mode: int, root: Path) -> Path:
    assert_under_root(path if path.exists() else path.parent, root)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    assert_under_root(path, root)
    st = path.stat()
    if stat.S_ISLNK(os.lstat(path).st_mode):
        raise PermissionDenied(f"directory is a symlink: {path}")
    if st.st_mode & 0o777 != mode:
        os.chmod(path, mode)
    return path


def chmod_file(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def assert_owner(path: Path, *, allow_foreign_owner: bool) -> None:
    if allow_foreign_owner:
        return
    try:
        uid = path.stat().st_uid
    except OSError as exc:
        raise PermissionDenied(f"cannot stat {path}") from exc
    if uid != os.geteuid():
        raise PermissionDenied(f"foreign owner on {path}")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(token: str, expected_hash: str) -> bool:
    got = hashlib.sha256(token.encode("utf-8")).digest()
    try:
        exp = bytes.fromhex(expected_hash)
    except ValueError:
        return False
    if len(got) != len(exp):
        return False
    return hmac.compare_digest(got, exp)


def load_allowlist(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def authorize_send(
    *,
    allowlist: dict[str, Any],
    from_peer: str,
    to_peer: str,
    token: str | None,
) -> None:
    if not allowlist:
        return
    entry = allowlist.get(from_peer)
    if not isinstance(entry, dict):
        raise PermissionDenied(f"peer {from_peer} is not on the allowlist")
    expected = entry.get("token_hash")
    if expected:
        if not token:
            raise PermissionDenied("token required")
        if not tokens_match(token, str(expected)):
            raise PermissionDenied("token mismatch")
    allowed = entry.get("can_send_to")
    if allowed is not None and to_peer not in allowed:
        raise PermissionDenied(f"{from_peer} cannot send to {to_peer}")
