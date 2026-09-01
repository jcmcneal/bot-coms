# bot-coms protocol 1.0

Protocol id: `bot-coms`. Envelope `schema_version`: `"1.0"`.

Receivers **must** reject unknown major versions (`"2.x"`). Unknown optional fields in `"1.x"` **must** be ignored. Wire format is UTF-8 JSON, one message per file, filename `<id>.json`. Timestamps are ISO-8601 UTC with a `Z` suffix (millisecond precision allowed).

Version bumps require a PROTOCOL.md change and validator tests before code lands.

## Delivery and effects

- Delivery: **at-least-once**.
- Effects: **exactly-once** when handlers consult the idempotency store (or are themselves idempotent) before side effects.
- No silent drop: every terminal state leaves an `acked/` or `dead-letter/` artifact.

Handlers that are not idempotent **will** duplicate side effects if they ignore the store. The transport cannot save you.

## Envelope

Required fields:

| Field | Rule |
|---|---|
| `schema_version` | string; major must be `1` |
| `id` | ULID (Crockford base32, 26 chars) |
| `idempotency_key` | non-empty string; API defaults to `id` |
| `correlation_id` | non-empty string; API defaults to `id` |
| `from` | peer id |
| `to` | peer id |
| `type` | `request` \| `response` \| `event` |
| `payload` | JSON object; serialized size ≤ `max_payload_bytes` (default 1 MiB) |
| `created_at` | UTC `Z` timestamp |
| `expires_at` | UTC `Z` timestamp; must be `> created_at` |
| `attempt` | integer `≥ 0` |

Optional: `reply_to` (peer id), `next_visible_at` (after nack), `priority` (int, default 0; **higher = sooner**), `headers` (string map, size-capped), transport-filled `receipt` `{enqueued_at, path}`.

`ack` is a **lifecycle transition**, not an envelope type.

Peer ids match `^[a-z][a-z0-9_-]{0,63}$`. Ids must not contain path separators or `..`.

## Peer-root layout

`$SPOOL_ROOT/<peer_id>/`:

```
inbox/          eligible + not-yet-visible messages
outbox/         sender receipts (immutable copies)
processing/     exclusively claimed
acked/          terminal success copies
results/        local result mirrors keyed by correlation_id
dead-letter/    terminal failure + reason wrapper
tmp/            staging for atomic writes (never durable truth)
state/
  spool.json
  allowlist.json
  idempotency.sqlite
  leases/<id>.json
  audit.jsonl
```

Message files are always `<ulid>.json`. Dead-letter wrapper:

```json
{
  "reason": "expired|max_attempts|validation|permission|handler_error|poison",
  "moved_at": "...",
  "last_error": "...",
  "envelope": {}
}
```

## Atomic enqueue

1. Write JSON under the **same peer** `tmp/` as `<ulid>.<pid>.<rand>.partial`.
2. `flush` + `fsync` the file; best-effort `fsync` of `tmp/`.
3. `os.replace` into the destination directory (never copy). Same-filesystem rename is atomic.
4. Best-effort `fsync` of the destination directory.
5. On failure: unlink the partial. Inbox must never show half-written files.

`send`:

1. Validate; default `id`, timestamps, `idempotency_key`, `correlation_id`.
2. Authz: `from` token hash + `to` on allowlist (empty allowlist = open loopback).
3. Atomic write `$to/inbox/<id>.json`.
4. Atomic receipt `$from/outbox/<id>.json` (same content + `receipt`).
5. Audit `enqueued`.

Crash before step 3: no inbox visibility. Crash between 3 and 4: inbox present, outbox missing — treated as enqueued-at-receiver. No auto-repair in MVP.

## Claim, lease, reclaim

Claim is exclusive because the **source file disappears**:

1. Run reclaim sweeper (stale leases).
2. List `inbox/*.json` sorted by priority desc, then ULID name.
3. Skip if `now >= expires_at` → dead-letter `expired`.
4. Skip if `now < next_visible_at`.
5. `os.replace(inbox/id → processing/id)`; `FileNotFoundError` means another worker won.
6. Create `state/leases/<id>.json`: `{worker_id, claimed_at, heartbeat_at, pid}`.
7. Return the claimed message.

Heartbeat rewrites the lease every `heartbeat_interval_s` (default 15). Lease timeout is 60s.

Reclaim: if a `processing/` file has no lease or `now - heartbeat_at > lease_timeout_s`, `os.replace(processing → inbox)` **without** bumping `attempt`, delete the lease, audit `reclaimed`. Pure crash is not a logical failure; only explicit nack bumps `attempt`.

## Ack / nack / result

| Op | FS effect |
|---|---|
| `ack` | `processing → acked`; delete lease; idempotency `completed`; audit |
| `ack` + result | for `request` and `event`, as ack + `type=response` envelope (`correlation_id`, `headers` preserved, `attempt=0`) into `reply_to` inbox (or original `from` if `reply_to` is null) **and** mirror `$claimer/results/<correlation_id>.json` and `$reply_to/results/<correlation_id>.json`; for `response`, the result is ignored and the response is terminal |
| `nack` | bump `attempt`; if `≥ max_attempts` → dead-letter `max_attempts`; else set `next_visible_at = now + backoff(attempt)` and `processing → inbox`; delete lease |
| poison | dead-letter `poison` (non-retryable) |

## Retry / TTL

Defaults: `base=1s`, `max=60s`, `multiplier=2.0`, **full jitter**:

`delay = random.uniform(0, min(max, base * multiplier**attempt))`

Max attempts: 5. Default TTL: 86400s from `created_at` if `expires_at` omitted. TTL is evaluated at claim time and on processing messages that outlive TTL.

## Idempotency

SQLite at `$peer/state/idempotency.sqlite`, primary key `(peer, idem_key)`.

1. Transport may deliver the same logical message more than once.
2. Before side effects, `Worker` calls `idempotency.begin(key)` (handlers may also check).
3. Successful ack records `completed` + result JSON/digest.
4. Duplicate enqueue with the same `(to, idempotency_key)` **accepts** a new `id` file; claim/worker **no-ops** the handler and acks immediately with the stored result.
5. Concurrent `begin` while `in_progress`: fail closed. Crash leaves `in_progress` until reclaim + retry (`abort` on nack).
6. Completed keys older than 7 days are GC'd.

## Security

- Directories `0o700`, files `0o600`; `chmod` after create so umask cannot widen.
- `Path.resolve()` jail: reject if realpath escapes spool root (symlink targets outside fail closed).
- MVP: **single OS user owns the spool**. If `stat().st_uid != os.geteuid()` and `allow_foreign_owner` is false, refuse.
- Allowlist tokens stored as SHA-256 only; compare in constant time; never write raw tokens to spool/audit/logs.
- Local POSIX only. NFS exclusive rename is unreliable and unsupported.

## Observability

Structured JSON to stderr plus `$peer/state/audit.jsonl`. Events: `enqueued|claimed|heartbeat|acked|nacked|reclaimed|dead_lettered`. Never log tokens or payloads by default.

## Ordering and concurrency

Best-effort FIFO by ULID within one peer inbox; higher `priority` first. Multiple workers per peer; exclusive rename is the lock. No cross-message transactions. No global cross-peer order.
