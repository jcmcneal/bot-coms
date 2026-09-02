"""init_spool, enqueue, list_inbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot_coms.atomic import read_json, write_json_atomic
from bot_coms.config import SpoolConfig
from bot_coms.envelope import envelope_from_dict, format_ts, new_envelope, validate_peer_id
from bot_coms.obs import audit
from bot_coms.paths import PEER_SUBDIRS, PeerPaths, peer_paths
from bot_coms.permissions import (
    assert_owner,
    assert_under_root,
    authorize_send,
    ensure_dir,
    load_allowlist,
)
from bot_coms.types import Clock, Envelope, PeerNotFound, SystemClock, ValidationError


def init_spool(
    spool_root: Path,
    peers: list[str],
    *,
    config: SpoolConfig | None = None,
    clock: Clock | None = None,
) -> Path:
    config = config or SpoolConfig()
    clock = clock or SystemClock()
    spool_root = Path(spool_root)
    spool_root.mkdir(parents=True, exist_ok=True)
    os_chmod_root(spool_root, config.dir_mode)
    assert_owner(spool_root, allow_foreign_owner=config.allow_foreign_owner)
    assert_under_root(spool_root, spool_root)
    for peer in peers:
        validate_peer_id(peer)
        paths = peer_paths(spool_root, peer)
        for rel in PEER_SUBDIRS:
            ensure_dir(paths.peer_root / rel if not rel.startswith("state/") else paths.peer_root / rel, mode=config.dir_mode, root=spool_root)
        # state/leases is in PEER_SUBDIRS as "state/leases"
        ensure_dir(paths.peer_root, mode=config.dir_mode, root=spool_root)
        ensure_dir(paths.leases, mode=config.dir_mode, root=spool_root)
        snapshot = {
            "created_at": format_ts(clock.now()),
            **config.to_public_dict(),
        }
        if not paths.spool_json.exists():
            write_json_atomic(
                paths.state,
                "spool.json",
                snapshot,
                tmp_dir=paths.tmp,
                root=spool_root,
                file_mode=config.file_mode,
            )
        if not paths.allowlist.exists():
            write_json_atomic(
                paths.state,
                "allowlist.json",
                {},
                tmp_dir=paths.tmp,
                root=spool_root,
                file_mode=config.file_mode,
            )
        if not paths.audit.exists():
            paths.audit.write_text("", encoding="utf-8")
            os_chmod_root(paths.audit, config.file_mode)
        assert_owner(paths.peer_root, allow_foreign_owner=config.allow_foreign_owner)
    return spool_root.resolve()


def os_chmod_root(path: Path, mode: int) -> None:
    import os

    os.chmod(path, mode)


def require_peer(spool_root: Path, peer_id: str) -> PeerPaths:
    validate_peer_id(peer_id)
    paths = peer_paths(Path(spool_root), peer_id)
    if not paths.peer_root.is_dir():
        raise PeerNotFound(peer_id)
    return paths


def enqueue_inbox(
    dest: PeerPaths,
    env: Envelope,
    *,
    config: SpoolConfig,
    clock: Clock,
) -> Path:
    assert_under_root(dest.inbox, dest.root)
    return write_json_atomic(
        dest.inbox,
        f"{env.id}.json",
        env.to_dict(),
        tmp_dir=dest.tmp,
        root=dest.root,
        file_mode=config.file_mode,
    )


def write_outbox_receipt(
    sender: PeerPaths,
    env: Envelope,
    inbox_path: Path,
    *,
    config: SpoolConfig,
    clock: Clock,
) -> Path:
    receipted = env.to_dict()
    receipted["receipt"] = {"enqueued_at": format_ts(clock.now()), "path": str(inbox_path)}
    return write_json_atomic(
        sender.outbox,
        f"{env.id}.json",
        receipted,
        tmp_dir=sender.tmp,
        root=sender.root,
        file_mode=config.file_mode,
    )


def send_message(
    spool_root: Path,
    *,
    from_peer: str,
    to: str,
    msg_type: str,
    payload: dict[str, Any],
    config: SpoolConfig,
    clock: Clock,
    token: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    reply_to: str | None = None,
    ttl_s: float | None = None,
    priority: int = 0,
    headers: dict[str, str] | None = None,
) -> Envelope:
    sender = require_peer(spool_root, from_peer)
    dest = require_peer(spool_root, to)
    assert_owner(sender.peer_root, allow_foreign_owner=config.allow_foreign_owner)
    assert_owner(dest.peer_root, allow_foreign_owner=config.allow_foreign_owner)
    allowlist = load_allowlist(sender.allowlist)
    authorize_send(allowlist=allowlist, from_peer=from_peer, to_peer=to, token=token)
    env = new_envelope(
        from_peer=from_peer,
        to=to,
        msg_type=msg_type,
        payload=payload,
        clock=clock,
        ttl_s=ttl_s if ttl_s is not None else config.default_ttl_s,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        reply_to=reply_to,
        priority=priority,
        headers=headers,
        max_payload_bytes=config.max_payload_bytes,
    )
    inbox_path = enqueue_inbox(dest, env, config=config, clock=clock)
    write_outbox_receipt(sender, env, inbox_path, config=config, clock=clock)
    audit(sender, clock, "enqueued", file_mode=config.file_mode, msg_id=env.id, to=to)
    from bot_coms.doorbell import after_enqueue

    after_enqueue(env)
    return env


def list_inbox(paths: PeerPaths, *, config: SpoolConfig) -> list[Envelope]:
    found: list[Envelope] = []
    if not paths.inbox.is_dir():
        return found
    for path in sorted(paths.inbox.glob("*.json")):
        assert_under_root(path, paths.root)
        try:
            env = envelope_from_dict(read_json(path), max_payload_bytes=config.max_payload_bytes)
        except (ValidationError, OSError, ValueError):
            continue
        found.append(env)
    found.sort(key=lambda e: (-e.priority, e.id))
    return found
