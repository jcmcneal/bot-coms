"""Lease create / heartbeat / reclaim sweeper."""

from __future__ import annotations

import os
from pathlib import Path

from bot_coms.atomic import read_json, write_json_atomic
from bot_coms.config import SpoolConfig
from bot_coms.envelope import format_ts, parse_ts
from bot_coms.obs import audit
from bot_coms.paths import PeerPaths
from bot_coms.permissions import assert_under_root
from bot_coms.types import Clock


def write_lease(
    paths: PeerPaths,
    msg_id: str,
    *,
    worker_id: str,
    clock: Clock,
    config: SpoolConfig,
    heartbeat_only: bool = False,
    claimed_at: str | None = None,
) -> None:
    now = format_ts(clock.now())
    body = {
        "worker_id": worker_id,
        "claimed_at": claimed_at or now,
        "heartbeat_at": now,
        "pid": os.getpid(),
    }
    if heartbeat_only and paths.lease_path(msg_id).is_file():
        prev = read_json(paths.lease_path(msg_id))
        if isinstance(prev, dict) and prev.get("claimed_at"):
            body["claimed_at"] = prev["claimed_at"]
            body["worker_id"] = prev.get("worker_id", worker_id)
    write_json_atomic(
        paths.leases,
        f"{msg_id}.json",
        body,
        tmp_dir=paths.tmp,
        root=paths.root,
        file_mode=config.file_mode,
    )
    if not heartbeat_only:
        audit(paths, clock, "claimed", file_mode=config.file_mode, msg_id=msg_id, worker_id=worker_id)
    else:
        audit(paths, clock, "heartbeat", file_mode=config.file_mode, msg_id=msg_id, worker_id=worker_id)


def delete_lease(paths: PeerPaths, msg_id: str) -> None:
    path = paths.lease_path(msg_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _lease_stale(lease_path: Path, now, timeout_s: float) -> bool:
    if not lease_path.is_file():
        return True
    try:
        data = read_json(lease_path)
        hb = parse_ts(str(data["heartbeat_at"]))
    except Exception:
        return True
    return (now - hb).total_seconds() > timeout_s


def reclaim_stale(paths: PeerPaths, clock: Clock, config: SpoolConfig) -> list[str]:
    reclaimed: list[str] = []
    now = clock.now()
    processing = paths.processing
    if not processing.is_dir():
        return reclaimed
    for msg_path in processing.glob("*.json"):
        assert_under_root(msg_path, paths.root)
        msg_id = msg_path.stem
        lease_path = paths.lease_path(msg_id)
        if not _lease_stale(lease_path, now, config.lease_timeout_s):
            continue
        dest = paths.inbox / msg_path.name
        try:
            os.replace(str(msg_path), str(dest))
        except FileNotFoundError:
            continue
        delete_lease(paths, msg_id)
        audit(paths, clock, "reclaimed", file_mode=config.file_mode, msg_id=msg_id)
        reclaimed.append(msg_id)
    # leases without a processing file
    if paths.leases.is_dir():
        for lease in paths.leases.glob("*.json"):
            if not (paths.processing / f"{lease.stem}.json").exists():
                delete_lease(paths, lease.stem)
    return reclaimed
