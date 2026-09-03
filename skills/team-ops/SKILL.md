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
| Assigner | `team_assign` | Hermes only. Dispatch slice or mid-task question (async, no wait). Never from Cursor. |
| Any | `team_bus` | status, list, verdict, log, `write_doc` (TEAM.md / STANDING.md), verify handle |
| Worker | `team_inbox` | on assign doorbell |
| Worker | `bot_coms_ack` | after cursor launch + status stamp (RUNNING = no wake) |
| Worker | `team_report` | after runner wake → doorbell return address (`from`) |
| Worker | `cursor_screen` | product work only |

**Reply stack:** address with envelope `from`/`to`. Report back to whoever mailed
you — not PM→Morgan, not SWE→EM→PM, not roster “who do I report to.”

Doorbell after spool put: assign → `hermes chat -Q`; terminal fold with
`headers.source=discord:…` → `hermes send --to {source}` (origin surface).
RUNNING does not ring. Cursor EXIT → `bot-coms job-done` (sidecar +
`peers.yaml`, no allowlist). Pulse cron (`b0tc0mspu15e`) is **not** the
control plane — disable it; pulse is a stuck-lease stub only.

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
- Product code: one Cursor session, investigate → plan → implement in a worktree (not main). Not ask-then-respawn.
- No synchronous `ask_team`. Cursor questions are PAUSED: Hermes `team_assign`s async, then resumes the same session.

## Mid-task questions (Cursor)

If Cursor needs peer guidance mid-slice:

1. Treat the job as **PAUSED** (not terminal, not blocked).
2. Hermes `team_assign`s the question to PM or the relevant peer — fire-and-forget.
3. Keep unblocked work moving. Do not sit in a wait loop.
4. When the answer arrives, resume the **same** Cursor session with that answer.
5. Unattended in-Cursor question prompts are auto-skipped — do not rely on them.
6. Escalate to Jason only after peer/fallback routes, and only for a real decision.

`team_assign` is Hermes-to-Hermes. Cursor never calls it.

## References

- [`references/operating-loop.md`](references/operating-loop.md) — from/to addressing
- [`references/cursor-cli.md`](references/cursor-cli.md) — Cursor CLI recipes
- [`references/provision.md`](references/provision.md) — new bot wiring
- [`references/soul-templates/`](references/soul-templates/) — role identity templates

Parent constitution: `~/.hermes/TEAM.md` (thin — this skill is the manual).
