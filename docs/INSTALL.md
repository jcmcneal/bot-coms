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
| `BOT_COMS_DEFAULT_SOURCE` | Optional `headers.source` stamp for cli/tui assigns; falls back to team/profile dotenv |
| `BOT_COMS_NOTIFY_ARGV` | Optional JSON argv for legacy `bot_coms.notify:source_argv` (not the reply-stack control plane) |
| `BOT_COMS_DOORBELL` | `1` (default) ring Hermes/adapter after spool put; set `0` in tests |
| `BOT_COMS_SPM_PING` | Override path to SPM webhook adapter (default `~/.hermes/team/ping-spm.sh`) |

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

Required at tool-call time: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`. Optional:
`BOT_COMS_TOKEN`, `BOT_COMS_DEFAULT_SOURCE`, `BOT_COMS_NOTIFY_ARGV` (PM notify worker).
