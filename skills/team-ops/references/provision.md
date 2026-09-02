# Team provision

When adding or re-aligning a Hermes team bot.

## Checklist

1. Jason approves role → update `team/ORG.md` (human org notes only; bots do not
   use it for reply-stack parents)
2. Profile config: `team_bus` + `bot_coms` toolsets; plugins `bot-coms` + `bot-coms-board` + `cursor-screen` (if executor)
3. Env: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`
4. Optional: peer id → Hermes profile in `~/.hermes/team/peers.yaml` (doorbell routing)
5. Out-of-band returns: adapter scripts (e.g. `~/.hermes/team/ping-spm.sh`); never commit `.ping-spm`
6. SOUL from `references/soul-templates/<posture>.md`
7. `skills.external_dirs`: `/Users/jason/projects/bot-coms/skills`
8. Smoke: `team_assign` with `report_only` intent → worker handles without Cursor
9. Pulse: install `scripts/bot-coms-pulse.sh` as stuck-lease stub only; **disable**
   LaunchAgent/cron job `b0tc0mspu15e` (doorbell is enqueue-time, not cron)

## Postures

| Posture | Profiles | Cursor |
|---|---|---|
| coordinator | PM | optional write/ask |
| executor | SWE | ask→plan→force |
| auditor | verifier | no forced cursor |
| researcher | DNA | ask/plan only |

Non-PM: Discord off, `gateway.multiplex_profiles: false`.

Do not use `a2a_call`. Leave dormant a2a YAML blocks. No Discord `hermes send`
as the team control plane.
