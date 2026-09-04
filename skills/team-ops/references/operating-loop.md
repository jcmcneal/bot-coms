# Operating loop

Parent: `team-ops` skill. Contract: `bot-coms/docs/BOARD.md`.

## Addressing (reply stack)

Envelope fields **`from`** and **`to`** are the address. Return address is
`from` (ack fold: `reply_to` or original `from`). Nested assigns nest the
stack. Do not read `peers.yaml` / `ORG.md` for “who do I report to.”

| Mail | Effect |
|---|---|
| A → B `assign` | spool/B + doorbell B (`chat -Q`) |
| B → A `report` | spool/A + doorbell A |
| Terminal + `headers.source=discord:…` | `hermes send --to {source}` (not `chat -Q`) |
| RUNNING-only ack | stamp SQL; **no** wake |
| Out-of-band `headers.source` | adapter (e.g. `ping-spm.sh`) |

Env: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`. Optional peer↔profile map in
`peers.yaml` is **routing** for Hermes `-p` and `bot-coms job-done` only.

## Assign

1. Write `~/.hermes/team/context/<SLICE>.md`
2. `team_assign` (slice, to_peer, title, assignment_path, tags) — fire-and-forget
3. End turn (doorbell already rang `to`)
4. Assignee stamps `RUNNING` + `active_job` (RUNNING ack does not wake)
5. After `team_report`, `LANDED`/`FAIL` doorbell the return address

`team_bus slice` is inspect-only after assign. Pulse is not required to drain.

## Worker assign

Doorbell wakes on `assign`. Worker then:

1. `team_inbox` → `launch_agent` decision (`message_id`, assignment body)
2. ONE `agent_screen` with `slice=` in sidecar (backend from profile config)
3. `team_bus` `status` → `RUNNING` + `active_job`
4. `bot_coms_ack` (id = `message_id`, result = `{intent: ack, slice, status: RUNNING}`)

Assign message stays in `processing` until step 4.

## Worker non-assign

Board worker handles `report_only`, `cancel`, `report` in Python (no coding agent).
Pulse only reclaims stuck leases — disable cron job `b0tc0mspu15e`.

## Report

`team_report` (slice, verdict, evidence) — doorbells whoever assigned you.

## ASK self-slices

Owning bot may register `[ASK]` scout slices. Promote to product `S*` as needed.

## Fire-and-forget

After `agent_screen launch`, end the Hermes turn. Runner EXIT calls
`bot-coms job-done` (sidecar + `peers.yaml`) for whoever launched the job.
Discord origin returns via `hermes send --to`. Never `bot_coms_send` to a
hardcoded org parent.

## Context TTL

14d grace → archive; 90d purge. Script: `bus-context-prune.sh`.

## Mid-task questions

`team_assign` is Hermes-only. The coding agent does not call it. There is no `ask_team`.

One agent session per slice: investigate → plan → implement. Do not respawn
to change phase.

If the agent returns a question: **PAUSED**, not terminal. Hermes `team_assign`s
the question async to PM or the peer, keeps unblocked work, then resumes the
same session with `session_id`. Unattended question prompts are auto-skipped.
No nested waits. Jason only after peer/fallback, and only for a genuine decision.
