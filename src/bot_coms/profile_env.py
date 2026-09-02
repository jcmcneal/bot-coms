"""Load team/profile dotenv when Hermes did not export vars into the process."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DEFAULT_SOURCE_KEY = "BOT_COMS_DEFAULT_SOURCE"
_NOTIFY_ARGV_KEY = "BOT_COMS_NOTIFY_ARGV"


def hermes_team_root() -> Path:
    raw = os.environ.get("BOT_COMS_TEAM_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermes" / "team"


def hermes_profiles_root() -> Path:
    return Path.home() / ".hermes" / "profiles"


def peers_yaml_path() -> Path:
    raw = os.environ.get("BOT_COMS_PEERS_YAML", "").strip()
    if raw:
        return Path(raw).expanduser()
    return hermes_team_root() / "peers.yaml"


def read_dotenv_value(path: Path, key: str) -> str:
    """Read one ``KEY=value`` from a dotenv-style file."""
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value.strip()
    return ""


def _peer_profiles(peers_yaml: Path) -> dict[str, str]:
    """Parse ``peers: [{id, profile}, ...]`` without a YAML dependency."""
    if not peers_yaml.is_file():
        return {}
    try:
        lines = peers_yaml.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    profiles: dict[str, str] = {}
    current: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current.get("id") and current.get("profile"):
                profiles[current["id"]] = current["profile"]
            current = {}
            rest = stripped[2:].strip()
            if rest.startswith("id:"):
                current["id"] = rest.split(":", 1)[1].strip()
            continue
        match = re.match(r"^(id|profile)\s*:\s*(.+)$", stripped)
        if match:
            current[match.group(1)] = match.group(2).strip()
    if current.get("id") and current.get("profile"):
        profiles[current["id"]] = current["profile"]
    return profiles


def peer_profile(peer_id: str) -> str:
    peer_id = (peer_id or "").strip()
    if not peer_id:
        return ""
    return _peer_profiles(peers_yaml_path()).get(peer_id, "")


def profile_env_path(profile: str) -> Path:
    return hermes_profiles_root() / profile / ".env"


def dotenv_search_paths(*, peer_id: str | None = None) -> list[Path]:
    """Documented fallback files (env wins over all of these)."""
    paths: list[Path] = []
    explicit = os.environ.get("BOT_COMS_DEFAULT_SOURCE_FILE", "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    team_env = hermes_team_root() / ".env"
    paths.append(team_env)
    pid = (peer_id or os.environ.get("BOT_COMS_PEER_ID", "")).strip()
    profile = peer_profile(pid) if pid else ""
    if not profile:
        profile = os.environ.get("BOT_COMS_PEER_PROFILE", "").strip()
    if profile:
        paths.append(profile_env_path(profile))
    return paths


def resolve_env_var(key: str, *, peer_id: str | None = None) -> str:
    """Return ``key`` from the process env, else documented profile/team dotenv files."""
    raw = os.environ.get(key, "").strip()
    if raw:
        return raw
    for path in dotenv_search_paths(peer_id=peer_id):
        raw = read_dotenv_value(path, key)
        if raw:
            return raw
    return ""


def resolve_default_source(*, peer_id: str | None = None) -> str:
    return resolve_env_var(_DEFAULT_SOURCE_KEY, peer_id=peer_id)


def resolve_notify_argv_raw(*, peer_id: str | None = None) -> str:
    return resolve_env_var(_NOTIFY_ARGV_KEY, peer_id=peer_id)
