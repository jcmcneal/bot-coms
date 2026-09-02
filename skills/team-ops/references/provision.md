# Team provision

When adding or re-aligning a Hermes team bot.

## Checklist

1. Jason approves role → update `team/ORG.md`
2. Profile config: `team_bus` + `bot_coms` toolsets; plugins `bot-coms` + `bot-coms-board` + `cursor-screen` (if executor)
3. Env: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`
4. PM only: `BOT_COMS_DEFAULT_SOURCE` (cli/tui assign fold target, e.g. `discord:<channel>`),
   `BOT_COMS_NOTIFY_ARGV` (JSON argv array; see `adapters/hermes_bot_coms/README.md`)
5. SOUL from `references/soul-templates/<posture>.md`
6. `skills.external_dirs`: `/Users/jason/projects/bot-coms/skills`
7. Smoke: `team_assign` with `report_only` intent → worker handles without Cursor
8. Pulse script: `scripts/bot-coms-pulse.sh` (install to `~/.hermes/scripts/`)

## Postures

| Posture | Profiles | Cursor |
|---|---|---|
| coordinator | PM | optional write/ask |
| executor | SWE | ask→plan→force |
| auditor | verifier | no forced cursor |
| researcher | DNA | ask/plan only |

Non-PM: Discord off, `gateway.multiplex_profiles: false`.

Do not use `a2a_call`. Leave dormant a2a YAML blocks.
