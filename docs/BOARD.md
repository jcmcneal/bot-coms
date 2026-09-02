# Board contract (bot-coms-board)

Team coordination lives in **`bot_coms_board`**, an opt-in sibling package in the
bot-coms repo. The `bot_coms` transport core never imports it.

## Reply stack (FILO)

Addressing is envelope **`from` / `to`**. Nested assigns nest the stack: report
back to whoever mailed you. Do **not** read `peers.yaml` / `ORG.md` to decide a
parent. `peers.yaml` may still map peer id → Hermes profile for **routing** the
doorbell (not org chart).

```
A writes {from:A, to:B, intent:assign, …} → spool/B/inbox → doorbell B
B writes {from:B, to:A, intent:report, …} → spool/A/inbox → doorbell A
```

Return address is envelope `from` (ack fold uses `reply_to` or original `from`).
Origin surface is the return path: Discord-in → Discord-out.

Doorbell after a successful spool put:

| Envelope | Delivery |
|---|---|
| `assign` (spool peer) | fresh Hermes session `hermes -p <to-profile> chat -Q --query-file` (never `--continue` / `-c`) |
| Terminal fold (`report` / `LANDED` / `FAIL`) + `headers.source=discord:…` (telegram, …) | `hermes -p <to-profile> send --to {source}` — **not** `chat -Q` |
| RUNNING-only `ack` (incl. `type=response` + `status=RUNNING` with missing intent) | stamp SQL only — **no** wake |
| Out-of-band `headers.source` (`spm:…`, webhook, …) | matching adapter (e.g. `~/.hermes/team/ping-spm.sh`) |

Cursor EXIT (`hermes-team-ops` `cursor_screen` runner) calls
`bot-coms job-done <job>` directly: sidecar `~/.hermes/cursor-screen/<job>.json`
+ `peers.yaml` profile→peer map → `team_report`. No role allowlist. No
`wake-cli-job.sh` (install removes any live `~/.hermes/scripts/wake-cli-job.sh`).

Never print or commit `.ping-spm` secrets.

**Pulse cron is not the control plane.** `scripts/bot-coms-pulse.sh` is a
stuck-lease reclaim stub. Disable LaunchAgent/cron job id `b0tc0mspu15e`.

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
| `assign` | any peer | fire-and-forget; assignee inbox → launch → stamp handle → ack RUNNING |
| `report_only` | assigner | read context → ACK verdict → **no Cursor** |
| `report` | worker | completion doorbell to **return address** (`from` / assigner peer) |
| `cancel` | assigner | ACK cancelled |

**RUNNING is valid only with `active_job` set** in bus.sqlite before the
assigner treats dispatch as successful. RUNNING ack does not doorbell.

## High-level tools (prefer these)

| Tool | Who | Replaces |
|---|---|---|
| `team_assign` | assigner | register + send (async, no wait) |
| `team_inbox` | worker | claim + parse + slice + digest + dispatch |
| `team_report` | worker | verdict + emit report to return address |
| `team_bus` | any | raw board CRUD; PM `write_doc` for TEAM.md / STANDING.md |

## PM-owned docs (`team_bus write_doc`)

Constitution and standing rules live in allowlisted paths only:

| Doc | Path | Modes |
|---|---|---|
| Team constitution | `~/.hermes/TEAM.md` | `replace`, `append`, `upsert_section` |
| Standing rules file | `~/.hermes/team/STANDING.md` | `replace`, `append` |

**Do not** patch `TEAM.md` with Hermes `patch` / `write_file` — PM `HERMES_HOME` is
under `profiles/project-manager`, so the authoritative-home skip does not apply and
writes hit `protected_instruction_file`. Use `team_bus` `write_doc` instead.

BUS operational notes stay on `team_bus` `log`. Slice assignment letters stay
`write_file` under `team/context/<SLICE>.md` (already ungated).

## CLI

```bash
bot-coms-board assign --slice S9 --to-peer swe --title "…" --assignment-path …
bot-coms-board inbox --peer swe
bot-coms-board worker --peer swe    # report_only/cancel/report only
bot-coms-board report --slice S9 --from-peer swe --verdict LANDED --evidence "…"
bot-coms-board migrate [--dry-run]
```

## Assign sequence

1. Write `team/context/<SLICE>.md`
2. `team_assign` (register + send; does **not** wait for ACK) → doorbell `to`
3. End turn
4. Assignee stamps `RUNNING` + `active_job` in SQL; RUNNING ack does **not** wake
5. `LANDED` / `FAIL` + evidence arrive via `team_report` → doorbell return address

`team_bus slice` is **inspect-only** after assign — do not poll for completion.
Pulse is **not** required to drain the inbox.

## Worker receive

Doorbell wakes Hermes for `assign` (and other non-RUNNING mail). Board worker
handles `report_only` / `cancel` / `report` in Python (no Cursor).

Worker assign completion:

1. `team_inbox` → `launch_cursor` bundle (message stays claimed until ack)
2. ONE `cursor_screen` with `slice=` in sidecar
3. `team_bus status` → `RUNNING` + `active_job`
4. `bot_coms_ack` with `{intent: ack, slice, status: RUNNING}` (no wake)

## Envelope headers

- `correlation_id` = slice id
- `idempotency_key` = `{intent}:{slice}:{content_sha256_prefix8}`
- `headers.source` = origin return path (Discord/telegram → `hermes send`; SPM → adapter)
