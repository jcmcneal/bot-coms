---
name: messaging-setup
description: "Install and enable persistent bot DMs/groups (bot-coms messaging) on a Hermes host."
version: 0.3.0
platforms: [macos, linux]
tags: [hermes, bot-coms, messaging, setup]
---

# Persistent messaging setup

Help the operator install **bot-coms messaging** on this Hermes host so authenticated
clients can use persistent DMs and multi-bot groups. Follow this skill when present;
otherwise follow the same steps from `docs/INSTALL.md` § Persistent messaging in the
bot-coms repository.

**Board is not required.** Do not enable `bot-coms-board` merely for messaging.

## Principles

1. Prefer proposing exact commands and seeking **one** yes/no approval for the
   non-restarting install before running mutating commands.
2. Preserve every existing entry in `plugins.enabled` — only add missing names.
3. **Principal:** If the user message already contains
   `Operator principal (authenticated in this client): …`, use that value as
   `default_principals`. Do **not** ask them to open a browser or paste
   `/api/auth/me`. Only invent nothing — if the principal is missing, ask them to
   sign in again in their client (plain language), not to scavenger-hunt JSON.
4. Prefer `auto_enroll_profiles: true` so every Hermes profile (default +
   `profiles/*/`) appears in the messaging roster automatically. New profiles
   show up on the next config reload without rewriting `config.json`. Do not use
   peer name `inbox` for a bot profile.
5. Restart the dashboard/gateway only after the operator explicitly confirms it is
   safe to interrupt active work.
6. Installing packages alone does not unlock messaging. Readiness needs enabled
   plugins, valid `config.json`, a healthy backend-owned service, and API v1.

## Procedure

### 1. Install the package

Use the same Python environment that runs the Hermes dashboard.

Editable checkout:

```bash
/path/to/hermes/python -m pip install -e "/path/to/bot-coms[messaging]"
```

Or from GitHub:

```bash
/path/to/hermes/python -m pip install "bot-coms[messaging] @ git+https://github.com/jcmcneal/bot-coms.git"
```

Confirm `bot-coms-messaging` is on `PATH` and Hermes provides the backend plugin
session service (`tui_gateway.plugin_sessions`). A missing service requires a
compatible Hermes build; do not install a standalone worker as a fallback.

### 2. Install the dashboard plugin

```bash
bot-coms-messaging install-dashboard --hermes-root /absolute/shared-hermes-root
```

Use `--force` only when replacing an existing plugin path. Prefer symlink unless
the operator asks for `--copy`.

### 3. Enable plugins (shared instance, one-time)

Add `bot-coms` and `bot-coms-messaging` to the **shared instance**
`plugins.enabled` list while preserving all other entries. Messaging is a
**dashboard** plugin — new Hermes profiles do **not** need `bot-coms-messaging` in
their own `config.yaml`. It does not add model tools or change approval defaults.

### 4. Write configuration

Create `<hermes-root>/plugin-data/bot-coms-messaging/config.json` with owner-only
permissions. Prefer auto-enroll (see
[references/config.example.json](references/config.example.json)):

- Generate a stable `server_id` once.
- Set `default_principals` to the operator principal from the client seed (or a
  verified identity — never invent one).
- Set `auto_enroll_profiles: true` and leave `profiles` empty (or only list
  overrides such as `"enabled": false` for opt-outs).

Peers and profile ids are derived from Hermes profile names when auto-enroll is
on, so crews with overlapping `BOT_COMS_PEER_ID` values do not collide.

### 5. Prepare backend-owned execution

Execution belongs to the existing Hermes backend. Do not install a messaging,
wake, or reconciliation launchd/systemd sidecar. For an existing deployment,
follow [the backend migration procedure](../../docs/BACKEND.md): drain or
reconcile old execution, preserve SQLite and spool data, and retire the old
service registration as part of cutover. Participant sessions are resumed by
exact identity; never use a profile's globally latest session.

### 6. Restart when safe

After the operator confirms active work can be interrupted, restart the dashboard
(or `hermes gateway restart` when that is how this deployment remounts plugins).
Then ask them to open Messaging in their client and tap **Check again**.

Readiness requires authenticated access, eligible profiles, compatible API v1, and
a healthy backend-owned messaging service.

## Troubleshooting

### `Warning: Unknown toolsets: agent_screen, bot_coms, team_bus`

These are valid **Hermes plugin toolsets**, not bot-coms configuration names or
misspellings. This warning can occur on a legacy CLI execution path. The
backend-owned messaging path does not launch fresh CLI processes for replies.

Do **not** remove those toolsets, move them into `plugins.enabled`, or disable a
working plugin to silence the warning. Confirm the relevant plugins are enabled
and that `hermes tools list` recognizes their plugin toolsets. Then update Hermes
to a build that discovers enabled plugins before validating explicit toolsets.
Check whether an old worker registration is still active and complete the
documented backend cutover instead of recreating that legacy execution path.

## Optional: load this skill permanently

Point the profile’s `skills.external_dirs` at this repository’s `skills/` directory
so `messaging-setup` is available without pasting the checklist into a prompt.

## Do not

- Wipe or reorder unrelated `plugins.enabled` entries
- Ask the operator to paste `/api/auth/me` when the client already provided a principal
- Enable coding tools or change approvals just to turn on messaging
- Blindly relaunch interrupted messaging runs with unknown side effects
- Claim messaging is ready before capabilities report `state: ready`
