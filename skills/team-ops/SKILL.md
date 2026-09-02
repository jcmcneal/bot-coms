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
| PM | `team_assign` | dispatch slice (async, no wait) |
| PM | `team_bus` | status, list, verdict, log, verify handle |
| Worker | `team_inbox` | on assign doorbell |
| Worker | `bot_coms_ack` | after cursor launch + status stamp |
| Worker | `team_report` | after runner wake |
| Worker | `cursor_screen` | product work only |

Pulse: `assign` → wake only. `report_only`/`cancel`/`report` → `bot-coms-board worker`
on worker peers. PM notify: `bot-coms worker` + `source_argv` on terminal responses.

## Loop

1. PM writes `~/.hermes/team/context/<SLICE>.md`
2. PM `team_assign` → end turn (no wait)
3. PM receives `RUNNING` + `active_job` via notify (SWE ack fold-up)
4. SWE: `team_inbox` → ONE `cursor_screen` → `team_bus status` → `bot_coms_ack`
5. Runner wake: `team_report` (or wake script stamps automatically)
6. PM receives `LANDED`/`FAIL` + evidence via notify; `team_bus slice` is inspect-only
7. PM folds vault `Handoff.md`

Pulse: `assign` → wake only. Worker peers: `report_only`/`cancel`/`report` →
`bot-coms-board worker`. PM: `type=response` + notifiable `headers.source` →
`bot-coms worker` with `bot_coms.notify:source_argv`.

## Hard limits

- Never put assignment body in spool payload
- Never use legacy text PINGs
- Never ACK RUNNING without `active_job`
- Never launch verify Cursor job from a wake
- Never print `.env` / tokens
- Product code: ask → plan → force in worktree (not main)

## References

- [`references/operating-loop.md`](references/operating-loop.md) — peer keys, assign sequence
- [`references/cursor-cli.md`](references/cursor-cli.md) — Cursor CLI recipes
- [`references/provision.md`](references/provision.md) — new bot wiring
- [`references/soul-templates/`](references/soul-templates/) — role identity templates

Parent constitution: `~/.hermes/TEAM.md` (thin — this skill is the manual).
