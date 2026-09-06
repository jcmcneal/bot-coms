# Board contract

`bot_coms_board` composes the local POSIX spool with the global ledger at
`~/.hermes/team/bus.sqlite`; `BOT_COMS_TEAM_ROOT` overrides it for tests/deployments.
Transport core modules never import board modules.

See [WORKFLOWS.md](WORKFLOWS.md) for role-independent ownership, configurable review
policies, acceptance, reassignment, schema migration and recovery commands.

## Interfaces

- `team_assign`: register and durably dispatch; explicit activity, optional policy
  and parent_slice. Sender comes from runtime identity, never a default PM role.
- `team_inbox`: claim and parse commands; responses and `ack`/`fail` payloads are
  terminal receipts even if an adapter mislabeled their envelope type. Returns
  the assignment contract, message ID and lease token. Coordinate in Hermes; an
  execution activity may require a coding agent.
- `team_report`: submit actual outcome/evidence to the frozen return peer and
  required reviewers. Process exit alone is not an accepted delivery.
- `team_workflow`: describe, review, accept, reassign, reconcile.
- `team_bus`: inspect/status/log and legacy CRUD; cannot bypass new acceptance gates.

Slice/list views and workflow describe expose computed `open_incomplete` and
`incomplete_reason` fields. A parseable assignment checklist also includes a
compact `checklist_summary`. An unfinished checklist with no `active_job` remains
open; DONE/CANCELLED slices and slices with live jobs are not flagged. If a
checklist is absent or cannot be parsed, an open ledger status with no job remains
visible through the same signal. Reconcile reports a summary of these slices but
does not launch, accept or change them.

The lean envelope is `{"schema_version":"1.0","intent":"assign","slice":"S1"}`.
Intents remain assign/report_only/report/cancel. Bodies live in context files;
contracts and individual reviewer decisions live in SQLite. Legacy text PINGs and
unknown schema versions are rejected. Internal envelopes carry `delivery=internal`;
origin metadata never authorizes an early external broadcast.

The stored content digest fences assignment dispatch. `report_only` is a
status/context refresh: it reads the current context file without rejecting an
expected prose update as a stale assignment, and it never launches work. Changing
the assigned work itself still requires a new slice or explicit reassignment.
Use `report_only` plus the context file for an in-scope context refresh; custom
command intents such as `scope_update` remain invalid.

RUNNING requires an actual job handle for contracted work. PAUSED preserves the
resumable session in its sidecar while clearing active execution. Cancel stops
queued dispatch; it refuses active jobs until their owner pauses/stops them.

## Local instruction overrides

`team_bus write_doc` retains the existing document-edit permissions and allowlist:
`~/.hermes/TEAM.md` supports replace/append/upsert_section;
optional `~/.hermes/team/STANDING.md` supports replace/append. These files hold
local overrides; reusable policy lives in [docs/team](team/README.md). Operational notes use
`team_bus log`. Assignment letters use context files. Changing role bindings does
not change document permissions.
