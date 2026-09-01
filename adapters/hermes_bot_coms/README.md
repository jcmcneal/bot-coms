# Hermes adapter (bot-coms)

Thin tools plugin. It imports **only** `bot_coms` plus Hermes `PluginContext` at `register(ctx)` time.

It does **not** import the A2A platform plugin, read or write BUS.md, or touch Open Genome.

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

Tools: `bot_coms_send`, `bot_coms_claim`, `bot_coms_reclaim`, `bot_coms_ack`, `bot_coms_nack`, `bot_coms_status`.

## Pulse integration

Each pulse must call `bot_coms_reclaim` before deciding whether to start a
worker. A reclaimed message is returned to `inbox`, so it is work even when
there was no newly enqueued file. Treat a live Hermes process as healthy only
when it is making progress; a stuck process must be replaced after its lease
expires.

Responses are terminal receipts. Acknowledge them without a result payload;
the adapter also suppresses result-generated replies for response envelopes as
a safety net, preventing receipt loops.
