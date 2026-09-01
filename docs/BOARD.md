# Board contract (bot-coms-board)

Team coordination lives in **`bot_coms_board`**, an opt-in sibling package in the
bot-coms repo. The `bot_coms` transport core never imports it.

## Storage

| Store | Path | Purpose |
|---|---|---|
| Slice ledger | `~/.hermes/team/bus.sqlite` | register, status, verdict, active_job |
| Assignment bodies | `~/.hermes/team/context/<SLICE>.md` | prose letter |
| Spool | `~/.hermes/team/spool/` | doorbell envelopes |

Board paths are **global** (`~/.hermes/team/`). Tests may override via
`BOT_COMS_TEAM_ROOT`. Do not pass profile `HERMES_HOME` as a board root.

## Spool payload (lean doorbell)

```json
{ "schema_version": "1.0", "intent": "assign", "slice": "S9" }
```

Only three keys: `schema_version`, `intent`, `slice`. Metadata lives in SQL at
`register` time. Legacy text `PING read … BUS.md item …` is **rejected**.

## Intents

| intent | Sender | Receiver action |
|---|---|---|
| `assign` | PM | fire-and-forget ping; SWE inbox → launch → stamp handle → ack RUNNING |
| `report_only` | PM | read context → ACK verdict → **no Cursor** |
| `report` | worker | completion doorbell after runner wake |
| `cancel` | PM | ACK cancelled |

**RUNNING is valid only with `active_job` set** in bus.sqlite before PM treats
dispatch as successful.

## High-level tools (prefer these)

| Tool | Who | Replaces |
|---|---|---|
| `team_assign` | PM | register + send (async, no wait) |
| `team_inbox` | worker | claim + parse + slice + digest + dispatch |
| `team_report` | worker | verdict + emit report |
| `team_bus` | any | raw board CRUD |

## CLI

```bash
bot-coms-board assign --slice S9 --to-peer swe --title "…" --assignment-path …
bot-coms-board inbox --peer swe
bot-coms-board worker --peer swe    # report_only/cancel/report only
bot-coms-board report --slice S9 --from-peer swe --verdict LANDED --evidence "…"
bot-coms-board migrate [--dry-run]
```

## PM assign sequence

1. Write `team/context/<SLICE>.md`
2. `team_assign` (register + send; does **not** wait for ACK)
3. End turn
4. Poll `team_bus slice` until `status=RUNNING` **and** `active_job` is set

## Worker receive

Pulse **wakes Hermes only** for `assign`. Pulse runs `bot-coms-board worker` for
`report_only` / `cancel` / `report` (handled in Python, no Cursor).

SWE assign completion:

1. `team_inbox` → `launch_cursor` bundle (message stays claimed until ack)
2. ONE `cursor_screen` with `slice=` in sidecar
3. `team_bus status` → `RUNNING` + `active_job`
4. `bot_coms_ack` with `{intent: ack, slice, status: RUNNING}`

## Envelope headers

- `correlation_id` = slice id
- `idempotency_key` = `{intent}:{slice}:{content_sha256_prefix8}`
