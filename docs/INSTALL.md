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
| `BOT_COMS_PEERS_YAML` | Optional peers roster (default `~/.hermes/team/peers.yaml`); job-done + doorbell routing |

## Post-install cleanup

`wake-cli-job.sh` is retired. Agent EXIT calls `bot-coms job-done` from the
`agent_screen` runner. Remove any leftover install copy:

```bash
rm -f ~/.hermes/scripts/wake-cli-job.sh
```

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

The adapter imports only `bot_coms` plus Hermes `PluginContext`. It does not import A2A or read BUS.md.

Required at tool-call time: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`. Optional:
`BOT_COMS_TOKEN`, `BOT_COMS_DEFAULT_SOURCE`, `BOT_COMS_NOTIFY_ARGV` (PM notify worker).

## Persistent messaging

Authenticated DMs and multi-bot groups use the sibling package `bot_coms_messaging`
(same wheel). It depends on **bot-coms transport only** — do **not** enable
`bot-coms-board` just to get messaging. This package still does not rewrite
Hermes `plugins.enabled`.

### One-repo install

Run in the same Python environment that serves the Hermes dashboard:

```bash
/path/to/hermes/python -m pip install -e "/path/to/bot-coms[messaging]"
bot-coms-messaging install-dashboard --hermes-root /absolute/shared-hermes-root
```

`install-dashboard` symlinks (or `--copy`) the packaged dashboard plugin to
`<hermes-root>/plugins/bot-coms-messaging/dashboard/`. Pass `--force` to replace
an existing path.

### Enable and configure

1. Add `bot-coms` and `bot-coms-messaging` to the shared instance
   `plugins.enabled` list (preserve other entries). Enable bot-coms for
   participating profiles as required by that deployment. Messaging is a
   **dashboard** plugin; it does not add model tools.
2. Create `<hermes-root>/plugin-data/bot-coms-messaging/config.json` with
   owner-only permissions. Use generated immutable IDs and an explicit account
   allowlist:

```json
{
  "server_id": "generate-an-installation-uuid-once",
  "hermes_executable": "/absolute/path/to/hermes",
  "max_turns": 12,
  "run_timeout_seconds": 600,
  "profiles": [
    {
      "id": "generate-a-profile-uuid-once",
      "peer": "swe",
      "name": "swe",
      "display_name": "SWE",
      "enabled": true,
      "principals": ["provider:user-id"]
    }
  ]
}
```

Principal format is `provider:user_id`, or `provider:user_id:org_id` when an
organization ID exists (from Hermes `/api/auth/me`). Keep `server_id` and profile
`id` values stable across renames; a deleted/recreated profile needs a new ID.
The reserved spool peer `inbox` represents the authenticated dashboard client;
do not assign that peer name to a bot profile.

3. Supervise the worker (do not attach its lifetime to a mobile or desktop client):

```bash
/path/to/hermes/python -m bot_coms_messaging.worker \
  --root /absolute/shared-hermes-root/plugin-data/bot-coms-messaging
```

4. Restart the dashboard when active work can safely be interrupted. Clients
   should re-check messaging readiness after setup. Readiness needs
   authenticated access, eligible profiles, API v1, and a recent worker
   heartbeat.

API namespace: `/api/plugins/bot-coms-messaging/v1`.

### Messaging tests

```bash
pip install -e ".[messaging,dev]"
python -m pytest tests/messaging -q
```

Never run these against live Hermes `plugin-data`.
