# Operating loop

Parent: `team-ops` skill. Contract: `bot-coms/docs/BOARD.md`.

## Peer keys

| From | Peer id |
|---|---|
| PM | `swe`, `verifier`, `dna-researcher` |
| Workers | `pm` |

Env: `BOT_COMS_SPOOL_ROOT`, `BOT_COMS_PEER_ID`.

## PM assign

1. Write `~/.hermes/team/context/<SLICE>.md`
2. `team_assign` (slice, to_peer, title, assignment_path, tags) — fire-and-forget
3. End turn
4. `RUNNING` + `active_job` arrive via notify (not `team_bus` polling)
5. After `team_report`, `LANDED`/`FAIL` + evidence arrive via notify

`team_bus slice` is inspect-only after assign.

## Worker assign (SWE)

Pulse wakes on `assign` only. SWE then:

1. `team_inbox` → `launch_cursor` decision (`message_id`, assignment body)
2. ONE `cursor_screen` with `slice=` in sidecar
3. `team_bus` `status` → `RUNNING` + `active_job`
4. `bot_coms_ack` (id = `message_id`, result = `{intent: ack, slice, status: RUNNING}`)

Assign message stays in `processing` until step 4.

## Worker non-assign

Pulse runs `bot-coms-board worker` for `report_only`, `cancel`, `report` on worker
peers — handled in Python, no Cursor.

PM: pulse runs `bot-coms worker` with `bot_coms.notify:source_argv` for
`type=response` + notifiable `headers.source` (requires `BOT_COMS_NOTIFY_ARGV`).
Board worker on PM handles `report` events first; the folded response is notify.

## Report

`team_report` (slice, verdict, evidence) — or wake script stamps automatically.

## ASK self-slices

Owning bot may register `[ASK]` scout slices. PM promotes to product `S*`.

## Fire-and-forget

After `cursor_screen launch`, end the Hermes turn. Runner wake is report-only.
PM: Discord-fold. Never `bot_coms_send` to `pm` on own wake.

## Context TTL

14d grace → archive; 90d purge. Script: `bus-context-prune.sh`.
