# Provision a Hermes teammate

Use a fresh Hermes profile, a small role-specific SOUL, and one directory entry in
`~/.hermes/team/workflows.json`. Read [shared policy](../../../docs/team/TEAM.md)
from the checkout; local TEAM.md holds deployment constraints only. Start with empty memories. This uses the existing CLI; no separate team
provisioning command is needed.

For a new installation, use the [local settings templates](../../../docs/team/README.md).
Do not copy shared documents into Hermes.

## 1. Create a clean profile

Choose a stable peer ID, profile name, responsibility and needed capabilities.
Use the same peer/profile name when convenient; titles can change without changing
identity. Example: a new research teammate, `research-2`:

```sh
hermes profile create research-2 --no-alias --no-skills --description "Read-only research with primary-source evidence."
hermes -p research-2 setup
```

Configure the chosen model/provider through setup and its supported authentication
flow. Do not copy another profile's credentials. `--no-skills` intentionally opts
out of bundled skill syncing; add the shared team skill and selected domain skills
below. `--no-alias` keeps access explicit with `hermes -p research-2`.

Do not use `--clone`, `--clone-from`, `--clone-all`, or copy a live profile directory.
Even the config clone copies `memories/MEMORY.md` and `memories/USER.md`, as well as
SOUL, config, environment and skills. That propagates old incidents and identities.

## 2. Add only the profile's responsibilities and tools

Replace the generated SOUL.md with the closest template in
[`soul-templates/`](soul-templates/), replacing its bracketed identity/scope fields.
Postures describe permitted work, not titles or reporting parents:

| Posture | Responsibility | Optional agent access |
|---|---|---|
| coordinator | Delegate, collect evidence, manage assigned outcomes | ask/plan for explicit investigation |
| executor | Deliver assigned implementation through a coding agent | ask/plan plus authorized write/force |
| auditor | Independently assess submitted artifacts | ask/plan |
| researcher | Investigate claims and cite primary sources | ask/plan |

Leave MEMORY.md and USER.md empty or absent. Store durable personal facts only in
the owning profile’s fact store, following [profile rules](../../../docs/team/PROFILE-RULES.md). Keep
instructions in their source document, runtime settings in config, and task state
in the ledger or project artifacts.

Merge this common configuration into the new profile's `config.yaml`, preserving
the model/auth setup. It is a fragment, not a replacement for the entire file:

```yaml
plugins:
  enabled: [bot-coms, bot-coms-board]
platform_toolsets:
  cli: [team_bus, bot_coms, memory, file]
skills:
  external_dirs: [/Users/jason/projects/bot-coms/skills]
memory:
  memory_enabled: true
  user_profile_enabled: true
  provider: holographic
  memory_char_limit: 2200
  user_char_limit: 1375
gateway:
  multiplex_profiles: false
```

The shared Hermes runtime must have those plugins installed; enabling names alone
does not install them. Add domain tools/skills explicitly. For agent work, also
enable plugin `agent-screen`, CLI toolset `agent_screen`, and configure
`agent_screen.default_backend`, `allowed_modes` and backend defaults. Read-only
postures get `[ask, plan]`; grant write/force only for the authorized scope. For
write mode, use `agent_screen.write_roots` with the narrow intended workspaces.
Record backend/model preferences in config, not in copied memories.

Add these non-secret identity settings to the new profile's `.env`:

```dotenv
BOT_COMS_SPOOL_ROOT=/Users/jason/.hermes/team/spool
BOT_COMS_PEER_ID=research-2
```

Keep the fact database profile-local. Do not copy another profile's database,
sessions, cron, channel credentials or notification hooks. New internal teammates
need no Discord connection or separate gateway. Outward outcomes use the existing
owner-acceptance path; adding a teammate does not add another publisher.

## 3. Register identity and capabilities

Add this entry inside the existing `peers` object in
`~/.hermes/team/workflows.json`; preserve the rest of the configuration:

```json
"research-2": {"profile": "research-2", "capabilities": ["research"]}
```

Initialize its mailbox using the CLI installed in the Hermes runtime:

```sh
bot-coms init-spool --root /Users/jason/.hermes/team/spool --peers research-2
```

The JSON directory also routes doorbells to profiles; new entries do not need a
duplicate in legacy `peers.yaml`. Update human org notes if useful, but those notes
never determine return routing. Bind an existing responsibility to the new peer
only when transferring that responsibility; ensure its capabilities satisfy the
policy. Adding a capability alone does not create a required review gate.

Existing assignments retain their frozen worker, owner, return peer and reviewers.
Use owner-authorized `team_workflow reassign` for unfinished work; stop/pause active
execution first. See [workflow configuration](../../../docs/WORKFLOWS.md).

## 4. Verify before assigning product work

- Start a fresh `hermes -p research-2 chat`. Confirm the intended model, peer ID,
  shared skill, profile-local memory and available tools: `team_assign`,
  `team_inbox`, `team_report`, `team_workflow`, and `bot_coms_ack`.
- From the assigning peer, create one explicit `activity="coordinate"` smoke
  assignment to the new peer with no product edits. Ask it
  to report its responsibility and return path from the contract. It must handle
  this in Hermes without launching an agent, ACK with the inbox lease token, and
  submit evidence through `team_report`.
- Inspect `team_workflow describe`: correct sender/return peer, owner, activity and
  submission. Check the saved notification origin before the owner accepts: a root
  acceptance reports there, while receipts and submissions remain internal. CLI
  assignments can inherit a default origin even when headers are omitted.
  For an executor or newly
  bound reviewer, also check the intended delivery policy's capabilities and
  independent gates before dispatching implementation.
- Reuse the existing once-a-minute `bot-coms-board reconcile` service. Do not add
  per-profile pulse cron, LLM polling, A2A or historical-task resume jobs.

Provisioning is complete when the fresh teammate can receive, acknowledge and
report a bounded assignment through its contract, with no inherited memories.
