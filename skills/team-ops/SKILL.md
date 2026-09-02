---
name: team-ops
description: "Hermes team coordination — assign, inbox, report via bot-coms-board. Single operating manual."
version: 1.0.0
platforms: [macos]
tags: [hermes, team, bot-coms, board]
---

# Team ops

**Canonical operating manual.** Loaded via `skills.external_dirs` from
`/Users/jason/projects/bot-coms/skills`.

Contract: [`docs/BOARD.md`](../../docs/BOARD.md) in the bot-coms repo.

## Tools (use these — not manual claim/register recipes)

| Role | Tool | When |
|---|---|---|
| Assigner | `team_assign` | dispatch slice (async, no wait) → doorbell `to` |
| Any | `team_bus` | status, list, verdict, log, `write_doc` (TEAM.md / STANDING.md), verify handle |
| Worker | `team_inbox` | on assign doorbell |
| Worker | `bot_coms_ack` | after cursor launch + status stamp (RUNNING = no wake) |
| Worker | `team_report` | after runner wake → doorbell return address (`from`) |
| Worker | `cursor_screen` | product work only |

**Reply stack:** address with envelope `from`/`to`. Report back to whoever mailed
you — not PM→Morgan, not SWE→EM→PM, not roster “who do I report to.”

Doorbell after spool put wakes Hermes (`hermes -p <to-profile> chat -Q`). Pulse
cron (`b0tc0mspu15e`) is **not** the control plane — disable it; pulse is a
stuck-lease stub only. No Discord `hermes send`.

## Loop

1. Assigner writes `~/.hermes/team/context/<SLICE>.md` (Hermes `write_file` — ungated)
2. `team_assign` → end turn (doorbell assignee; no wait)
3. Assignee stamps `RUNNING` + `active_job` in SQL; RUNNING ack does not wake
4. Assignee: `team_inbox` → ONE `cursor_screen` → `team_bus status` → `bot_coms_ack`
5. Runner wake: `team_report` (doorbell return address)
6. Assigner receives `LANDED`/`FAIL` + evidence; `team_bus slice` is inspect-only
7. Fold vault `Handoff.md` / out-of-band adapter as needed (`ping-spm.sh` for SPM)

## Hard limits

- Never put assignment body in spool payload
- Never use legacy text PINGs
- Never ACK RUNNING without `active_job`
- Never launch verify Cursor job from a wake
- Never print `.env` / tokens / `.ping-spm` secrets
- Never look up org chart / `ORG.md` / roster for parent — use envelope `from`
- Constitution / standing rules: `team_bus write_doc` on `~/.hermes/TEAM.md` or
  `~/.hermes/team/STANDING.md` — never Hermes `patch` / `write_file` on TEAM.md
- Product code: ask → plan → force in worktree (not main)

## References

- [`references/operating-loop.md`](references/operating-loop.md) — from/to addressing
- [`references/cursor-cli.md`](references/cursor-cli.md) — Cursor CLI recipes
- [`references/provision.md`](references/provision.md) — new bot wiring
- [`references/soul-templates/`](references/soul-templates/) — role identity templates

Parent constitution: `~/.hermes/TEAM.md` (thin — this skill is the manual).
