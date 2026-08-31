# Install

## Library + CLI

```bash
cd /Users/jason/projects/bot-coms
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/unit tests/smoke -q
```

Env vars:

| Variable | Purpose |
|---|---|
| `BOT_COMS_SPOOL_ROOT` | Default `--root` |
| `BOT_COMS_PEER_ID` | Default `--peer` / `--from` |
| `BOT_COMS_TOKEN` | Allowlist token (never logged) |

## CLI smoke loopback

```bash
bot-coms init-spool --root /tmp/bot-coms-demo --peers a,b
echo '{"hello":"world"}' > /tmp/p.json
bot-coms send --root /tmp/bot-coms-demo --from a --to b --type request --payload-file /tmp/p.json
bot-coms claim --root /tmp/bot-coms-demo --peer b
bot-coms ack --root /tmp/bot-coms-demo --peer b --id <ID>
bot-coms smoke --all
```

## Hermes adapter (user-driven)

This package **does not** edit Hermes configuration.

1. `pip install -e /Users/jason/projects/bot-coms` into the same environment that runs Hermes.
2. `hermes plugins enable bot-coms` (your action).
3. Alternate drop-in (not performed unless you ask): symlink `adapters/hermes_bot_coms` to `~/.hermes/plugins/bot-coms`.

The adapter imports only `bot_coms` plus Hermes `PluginContext`. It does not import A2A, read BUS.md, or touch Open Genome.

Required at tool-call time: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`. Optional: `BOT_COMS_TOKEN`.
