"""claim / ack / nack / result / dead-letter."""

from __future__ import annotations

import os
from bot_coms.locking import locked
from datetime import timedelta
from typing import Any

from bot_coms.atomic import read_json, write_json_atomic
from bot_coms.config import SpoolConfig
from bot_coms.envelope import (
    SCHEMA_VERSION,
    envelope_from_dict,
    format_ts,
    generate_ulid,
    parse_ts,
    validate_peer_id,
)
from bot_coms.idempotency import IdempotencyStore
from bot_coms.lease import delete_lease, reclaim_stale, write_lease
from bot_coms.obs import audit
from bot_coms.paths import PeerPaths
from bot_coms.permissions import assert_under_root
from bot_coms.retry import backoff_delay, is_expired, is_visible
from bot_coms.spool import enqueue_inbox, require_peer
from bot_coms.types import (
    ClaimedMessage,
    Clock,
    Envelope,
    Expired,
    MessageState,
    ValidationError,
)


def _read_envelope(path, config: SpoolConfig) -> Envelope:
    return envelope_from_dict(read_json(path), max_payload_bytes=config.max_payload_bytes)


def dead_letter(
    paths: PeerPaths,
    env: Envelope,
    reason: str,
    *,
    config: SpoolConfig,
    clock: Clock,
    last_error: str = "",
    source_path=None,
) -> None:
    wrapper = {
        "reason": reason,
        "moved_at": format_ts(clock.now()),
        "last_error": last_error,
        "envelope": env.to_dict(),
    }
    write_json_atomic(
        paths.dead_letter,
        f"{env.id}.json",
        wrapper,
        tmp_dir=paths.tmp,
        root=paths.root,
        file_mode=config.file_mode,
    )
    if source_path is not None:
        try:
            Path_unlink(source_path)
        except OSError:
            pass
    delete_lease(paths, env.id)
    audit(
        paths,
        clock,
        "dead_lettered",
        file_mode=config.file_mode,
        msg_id=env.id,
        reason=reason,
    )


def Path_unlink(path) -> None:
    from pathlib import Path

    Path(path).unlink(missing_ok=True)


@locked
def claim(
    paths: PeerPaths,
    *,
    config: SpoolConfig,
    clock: Clock,
    worker_id: str,
    msg_id: str | None = None,
    store: IdempotencyStore | None = None,
) -> ClaimedMessage | None:
    reclaim_stale(paths, clock, config)
    now = clock.now()
    candidates = []
    if msg_id:
        target = paths.inbox / f"{msg_id}.json"
        if target.is_file():
            candidates = [target]
    else:
        candidates = sorted(paths.inbox.glob("*.json"), key=lambda p: p.name)

    scored: list[tuple[int, str, Any, Envelope]] = []
    for path in candidates:
        assert_under_root(path, paths.root)
        try:
            env = _read_envelope(path, config)
        except (ValidationError, OSError, ValueError) as exc:
            try:
                raw = read_json(path)
                fake = Envelope(
                    schema_version=SCHEMA_VERSION,
                    id=path.stem if len(path.stem) == 26 else generate_ulid(now),
                    idempotency_key="invalid",
                    correlation_id="invalid",
                    from_peer="invalid",
                    to=paths.peer_id,
                    type="event",
                    payload={},
                    created_at=format_ts(now),
                    expires_at=format_ts(now + timedelta(seconds=1)),
                    attempt=0,
                )
                if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                    fake.id = raw["id"]
                dead_letter(
                    paths,
                    fake,
                    "validation",
                    config=config,
                    clock=clock,
                    last_error=str(exc),
                    source_path=path,
                )
            except Exception:
                Path_unlink(path)
            continue
        if is_expired(env, now):
            dead_letter(paths, env, "expired", config=config, clock=clock, source_path=path)
            continue
        if not is_visible(env, now):
            continue
        scored.append((-env.priority, env.id, path, env))

    scored.sort()
    for _prio, _id, path, env in scored:
        dest = paths.processing / path.name
        try:
            os.replace(str(path), str(dest))
        except FileNotFoundError:
            continue
        token = write_lease(paths, env.id, worker_id=worker_id, clock=clock, config=config)
        return ClaimedMessage(envelope=env, peer_id=paths.peer_id, path=dest, worker_id=worker_id, lease_token=token)
    return None


def _assert_claim(paths, claimed):
    from bot_coms.types import ClaimLost
    if not claimed.path.is_file():
        raise ClaimLost(claimed.envelope.id)
    if claimed.lease_token:
        try:
            lease = read_json(paths.lease_path(claimed.envelope.id))
        except OSError:
            raise ClaimLost(claimed.envelope.id)
        if lease.get('token') != claimed.lease_token:
            raise ClaimLost(claimed.envelope.id)


def _expire_processing_if_needed(paths: PeerPaths, claimed: ClaimedMessage, config: SpoolConfig, clock: Clock) -> None:
    if is_expired(claimed.envelope, clock.now()):
        dead_letter(
            paths,
            claimed.envelope,
            "expired",
            config=config,
            clock=clock,
            source_path=claimed.path,
        )
        raise Expired(claimed.envelope.id)


@locked
def ack(
    paths: PeerPaths,
    claimed: ClaimedMessage,
    *,
    config: SpoolConfig,
    clock: Clock,
    store: IdempotencyStore | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    _assert_claim(paths, claimed)
    _expire_processing_if_needed(paths, claimed, config, clock)
    if not claimed.path.is_file():
        from bot_coms.types import ClaimLost

        raise ClaimLost(claimed.envelope.id)
    # Persist completion before publication. A reclaimed message replays the saved
    # result through Worker without repeating the handler's side effects.
    if store is not None:
        store.complete(paths.peer_id, claimed.envelope.idempotency_key, result, clock)
    if result is not None and claimed.envelope.type != "response":
        write_result(paths, claimed.envelope, result, config=config, clock=clock)
    dest = paths.acked / claimed.path.name
    os.replace(str(claimed.path), str(dest))
    delete_lease(paths, claimed.envelope.id)
    audit(paths, clock, "acked", file_mode=config.file_mode, msg_id=claimed.envelope.id)


def _result_id(message_id: str) -> str:
    import hashlib
    alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
    value = int.from_bytes(hashlib.sha256(('result:' + message_id).encode()).digest()[:16], 'big')
    chars = []
    for _ in range(26):
        chars.append(alphabet[value & 31])
        value >>= 5
    return ''.join(reversed(chars))


def write_result(
    claimer: PeerPaths,
    original: Envelope,
    result: dict[str, Any],
    *,
    config: SpoolConfig,
    clock: Clock,
) -> Envelope:
    reply_peer = original.reply_to or original.from_peer
    validate_peer_id(reply_peer)
    now = clock.now()
    response = Envelope(
        schema_version=SCHEMA_VERSION,
        id=_result_id(original.id),
        idempotency_key=f"result:{original.idempotency_key}",
        correlation_id=original.correlation_id,
        from_peer=claimer.peer_id,
        to=reply_peer,
        type="response",
        payload=result,
        created_at=format_ts(now),
        expires_at=format_ts(now + timedelta(seconds=config.default_ttl_s)),
        attempt=0,
        reply_to=None,
        # Preserve transport/application routing metadata (for example the
        # originating Discord channel or thread) across the result handoff.
        headers=dict(original.headers) if original.headers else None,
    )
    dest = require_peer(claimer.root, reply_peer)
    if not any((folder / f'{response.id}.json').exists() for folder in (dest.inbox, dest.processing, dest.acked, dest.dead_letter)):
        enqueue_inbox(dest, response, config=config, clock=clock)
    write_json_atomic(
        claimer.results,
        f"{original.correlation_id}.json",
        response.to_dict(),
        tmp_dir=claimer.tmp,
        root=claimer.root,
        file_mode=config.file_mode,
    )
    if dest.peer_id != claimer.peer_id:
        write_json_atomic(
            dest.results,
            f"{original.correlation_id}.json",
            response.to_dict(),
            tmp_dir=dest.tmp,
            root=dest.root,
            file_mode=config.file_mode,
        )
    from bot_coms.doorbell import after_enqueue

    after_enqueue(response)
    return response


@locked
def nack(
    paths: PeerPaths,
    claimed: ClaimedMessage,
    *,
    error: str,
    config: SpoolConfig,
    clock: Clock,
    store: IdempotencyStore | None = None,
    retryable: bool = True,
) -> None:
    _assert_claim(paths, claimed)
    env = claimed.envelope
    if not retryable:
        if store is not None:
            store.abort(paths.peer_id, env.idempotency_key)
        dead_letter(
            paths,
            env,
            "poison",
            config=config,
            clock=clock,
            last_error=error,
            source_path=claimed.path,
        )
        return
    env.attempt += 1
    if env.attempt >= config.retry.max_attempts:
        if store is not None:
            store.abort(paths.peer_id, env.idempotency_key)
        dead_letter(
            paths,
            env,
            "max_attempts",
            config=config,
            clock=clock,
            last_error=error,
            source_path=claimed.path,
        )
        return
    delay = backoff_delay(env.attempt, config.retry)
    env.next_visible_at = format_ts(clock.now() + timedelta(seconds=delay))
    write_json_atomic(
        paths.processing,
        claimed.path.name,
        env.to_dict(),
        tmp_dir=paths.tmp,
        root=paths.root,
        file_mode=config.file_mode,
    )
    os.replace(str(claimed.path), str(paths.inbox / claimed.path.name))
    delete_lease(paths, env.id)
    if store is not None:
        store.abort(paths.peer_id, env.idempotency_key)
    audit(
        paths,
        clock,
        "nacked",
        file_mode=config.file_mode,
        msg_id=env.id,
        attempt=env.attempt,
        error=error,
    )


@locked
def release_to_inbox(
    paths: PeerPaths,
    claimed: ClaimedMessage,
    *,
    config: SpoolConfig,
    clock: Clock,
    store: IdempotencyStore | None = None,
) -> None:
    """Clean shutdown: return to inbox without bumping attempt."""
    _assert_claim(paths, claimed)
    if claimed.path.is_file():
        os.replace(str(claimed.path), str(paths.inbox / claimed.path.name))
    delete_lease(paths, claimed.envelope.id)
    if store is not None:
        store.abort(paths.peer_id, claimed.envelope.idempotency_key)
    audit(paths, clock, "reclaimed", file_mode=config.file_mode, msg_id=claimed.envelope.id, reason="release")


def status(paths: PeerPaths, msg_id: str, *, config: SpoolConfig) -> MessageState:
    folders = (
        ("processing", paths.processing),
        ("inbox", paths.inbox),
        ("acked", paths.acked),
        ("dead-letter", paths.dead_letter),
        ("outbox", paths.outbox),
    )
    for name, folder in folders:
        path = folder / f"{msg_id}.json"
        if not path.is_file():
            continue
        data = read_json(path)
        if name == "dead-letter":
            env = None
            if isinstance(data, dict) and isinstance(data.get("envelope"), dict):
                try:
                    env = envelope_from_dict(data["envelope"], max_payload_bytes=config.max_payload_bytes)
                except ValidationError:
                    env = None
            return MessageState(
                id=msg_id,
                location=name,
                envelope=env,
                dead_letter_reason=data.get("reason") if isinstance(data, dict) else None,
                extra=data if isinstance(data, dict) else {},
            )
        try:
            env = envelope_from_dict(data, max_payload_bytes=config.max_payload_bytes)
        except ValidationError:
            env = None
        return MessageState(id=msg_id, location=name, envelope=env)
    return MessageState(id=msg_id, location="unknown")


def poll_result(paths: PeerPaths, correlation_id: str, *, config: SpoolConfig) -> Envelope | None:
    path = paths.result_path(correlation_id)
    assert_under_root(path if path.exists() else paths.results, paths.root)
    if not path.is_file():
        return None
    return envelope_from_dict(read_json(path), max_payload_bytes=config.max_payload_bytes)
