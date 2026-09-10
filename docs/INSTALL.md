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
| `BOT_COMS_COMPLETION_SINK_ARGV` | Optional JSON argv for `bot_coms.notify:completion_sink_argv`; `{route}` is response `to`, `{source}` is preserved `headers.source`, and stdin is the full response envelope |
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

## Asynchronous completion sink

An external owner of peer `b` can consume responses from work delegated to another
peer without bot-coms knowing anything about the messaging platform:

```bash
export BOT_COMS_COMPLETION_SINK_ARGV='["/opt/bot-owner/wake","--route","{route}","--source","{source}"]'
bot-coms worker --root /var/lib/bot-coms --peer b \
  --handler bot_coms.notify:completion_sink_argv
```

The command is invoked directly as argv (never through a shell) and receives the
complete response envelope as JSON on stdin. It must exit zero before the response
is acknowledged. A non-zero exit retries and eventually dead-letters under the
spool's Worker policy. The callback can be delivered more than once after a crash,
so deduplicate by envelope `id`. Non-responses and `delivery=internal` responses
are left untouched. The env value also follows the existing team/profile dotenv
fallback lookup. `--completion-sink-argv` may be repeated instead of setting the
env var.

## Hermes adapter (user-driven)

This package **does not** edit Hermes configuration.

1. `pip install -e /Users/jason/projects/bot-coms` into the same environment that runs Hermes.
2. `hermes plugins enable bot-coms` (your action).
3. Alternate drop-in (not performed unless you ask): symlink `adapters/hermes_bot_coms` to `~/.hermes/plugins/bot-coms`.

The adapter imports only `bot_coms` plus Hermes `PluginContext`. It does not import A2A or read BUS.md.

Required at tool-call time: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`. Optional:
`BOT_COMS_TOKEN`, `BOT_COMS_DEFAULT_SOURCE`, `BOT_COMS_NOTIFY_ARGV` (legacy PM
notify worker), `BOT_COMS_COMPLETION_SINK_ARGV` (structured response callback).

## Persistent messaging

Authenticated DMs and multi-bot groups use the sibling package `bot_coms_messaging`
(same wheel). Execution uses Hermes's public CLI session resume interface; do **not** enable
`bot-coms-board` just to get messaging. This package still does not rewrite
Hermes `plugins.enabled`.

### Agent-assisted path

If a Hermes profile can load skills from this repository’s `skills/` directory
(for example `skills.external_dirs: [/path/to/bot-coms/skills]`), ask it to follow
the **`messaging-setup`** skill. Clients may also open a new session with a seed
prompt that embeds this checklist and, when signed in, the operator’s already
authenticated principal (`provider:user_id` or `provider:user_id:org_id`). When
that principal is present in the prompt, do not ask the operator to open a
browser or paste `/api/auth/me`.

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

1. Add `bot-coms` and `bot-coms-messaging` to the **shared instance**
   `plugins.enabled` list (preserve other entries). Messaging is a **dashboard**
   plugin — enable it once on the shared Hermes root. New named profiles do **not**
   need `bot-coms-messaging` in their own `config.yaml`.
2. Create `<hermes-root>/plugin-data/bot-coms-messaging/config.json` with
   owner-only permissions. Prefer auto-enroll so every Hermes profile appears in
   the roster without rewriting the file for each new profile:

```json
{
  "server_id": "generate-an-installation-uuid-once",
  "max_turns": 12,
  "run_timeout_seconds": 600,
  "default_principals": ["provider:user-id"],
  "auto_enroll_profiles": true,
  "profiles": []
}
```

`default_principals` entries use `provider:user_id`, or `provider:user_id:org_id`
when an organization ID exists. Keep `server_id` stable across renames. With
`auto_enroll_profiles: true`, messaging discovers `default` plus live
`profiles/*/`, derives stable ids/peers from each Hermes profile name (not from
`BOT_COMS_PEER_ID`), and applies `default_principals`. Put explicit rows in
`profiles` only for overrides (for example `"enabled": false` to opt out).

Without auto-enroll, list every messaging profile explicitly (legacy). The
reserved spool peer `inbox` represents the authenticated dashboard client; do
not assign that peer name to a bot profile.

3. Use a Hermes build with `hermes chat --resume` and its dashboard
   lifecycle installation. The plugin starts its messaging scheduler inside
   the existing backend. Each participant resumes an exact conversation session.
   A build without the public CLI reports unavailable; it never falls back
   to launching CLI workers. See [Backend execution and migration](BACKEND.md).

4. Restart the dashboard when active work can safely be interrupted. Clients
   should re-check messaging readiness after setup. Readiness needs
   authenticated access, eligible profiles, API v1, and a healthy backend-owned
   messaging service. No separate launchd/systemd worker is needed.

API namespace: `/api/plugins/bot-coms-messaging/v1`.

### Messaging tests

```bash
pip install -e ".[messaging,dev]"
python -m pytest tests/messaging -q
```

Never run these against live Hermes `plugin-data`.
