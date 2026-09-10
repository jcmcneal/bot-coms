# Hermes adapter (bot-coms)

Thin tools plugin. It imports **only** `bot_coms` plus Hermes `PluginContext` at `register(ctx)` time.

It does **not** import the A2A platform plugin or read/write BUS.md.

## Install (user action)

```bash
pip install -e /Users/jason/projects/bot-coms
hermes plugins enable bot-coms
```

This repository does not rewrite Hermes configuration.

Alternate drop-in (only if you choose to):

```bash
ln -s /Users/jason/projects/bot-coms/adapters/hermes_bot_coms ~/.hermes/plugins/bot-coms
```

## Env

| Variable | Required at tool-call |
|---|---|
| `BOT_COMS_SPOOL_ROOT` | yes |
| `BOT_COMS_PEER_ID` | yes |
| `BOT_COMS_TOKEN` | no (allowlist) |

## Tool surfaces: caller vs worker

### Caller / originator peers (e.g. PM)

Prefer the higher-level tools for agent ergonomics:

| Tool | Semantics |
|---|---|
| `bot_coms_request` | **Synchronous** — send `type=request` with `reply_to` set, wait for correlated result, silently ack terminal response. Returns `{ok, correlation_id, payload, from}` or `{ok:false, error, reason}`. |
| `bot_coms_emit` | **Fire-and-forget** — enqueue without waiting. Default `type=event`. Returns `{ok, envelope}`. |
| `bot_coms_status` | Inspect one message or spool counts. |

`bot_coms_request` accepts optional `headers` (opaque string map). When `headers.source`
is omitted and the Hermes session is on a messaging platform, the adapter auto-stamps
`source` from `HERMES_SESSION_*` in `platform:chat_id[:thread_id]` form.

Structured error `reason` values: `timeout`, `dead_letter`, `permission`, `handler_error`.
Errors never include raw tokens or payload contents.

### Worker / intermediate peers (e.g. SWE, SUB)

Use the atomic lifecycle tools or the long-running CLI worker:

| Tool | Semantics |
|---|---|
| `bot_coms_reclaim` | **Pulse first** — return stale `processing/` messages to inbox. |
| `bot_coms_claim` | Claim next eligible inbox message (or by `id`). |
| `bot_coms_ack` | Ack claimed message; pass `result` to fold up to parent (requests/events only). |
| `bot_coms_nack` | Retry or poison a claimed message. |
| `bot_coms_send` | Low-level enqueue when delegating manually. |
| `bot_coms_status` | Debug / inspection. |

Intermediate peers that re-assign work should copy inbound `headers` unchanged and use
`ack(id, result=...)` exactly once when the child completes. Silent ack (no `result`) does
**not** fold up to the parent.

Python library equivalent for delegation: `delegate_and_ack(client, claimed, to, payload)`.

### Sync vs fire-and-forget contract

| Need | Library | Hermes tool |
|---|---|---|
| Ask a peer and wait for payload | `Client.request(...)` | `bot_coms_request` |
| Notify without expecting reply | `Client.fire(...)` | `bot_coms_emit` |
| Process inbox as worker | `Worker` / `bot-coms worker` CLI | `bot_coms_claim` + `bot_coms_ack` |

## Originator notify worker

On the peer that owns the human conversation (e.g. PM), run a dedicated worker that
delivers terminal `response` envelopes via a configured argv (no Discord imports):

```bash
bot-coms worker --peer pm --handler bot_coms.notify:source_argv \
  --notify-argv /Users/jason/.local/bin/hermes \
  --notify-argv send --notify-argv --to --notify-argv '{source}'
```

The worker reads the response `payload` from stdin of that argv. Intermediate peers
only fold results up the chain via `ack` + `result`; they do not notify humans.

For an asynchronous delegator whose external owner must wake and decide the next
step, use the structured handler instead:

```bash
export BOT_COMS_COMPLETION_SINK_ARGV='["/opt/bot-owner/wake","--route","{route}","--source","{source}"]'
bot-coms worker --peer swe --handler bot_coms.notify:completion_sink_argv
```

It receives the full response envelope on stdin, substitutes `route` from
envelope `to` and `source` from the preserved opaque header, skips internal mail,
and acknowledges only after exit zero. Failures use normal Worker retries and
dead-lettering. The callback must deduplicate by envelope id.

## Pulse integration

Each pulse must call `bot_coms_reclaim` before deciding whether to start a
worker. A reclaimed message is returned to `inbox`, so it is work even when
there was no newly enqueued file. Treat a live Hermes process as healthy only
when it is making progress; a stuck process must be replaced after its lease
expires.

Responses are terminal receipts. Acknowledge them without a result payload;
the adapter also suppresses result-generated replies for response envelopes as
a safety net, preventing receipt loops.
