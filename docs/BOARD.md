# Board contract

`bot_coms_board` composes the local POSIX spool with the global ledger at
`~/.hermes/team/bus.sqlite`; `BOT_COMS_TEAM_ROOT` overrides it for tests/deployments.
Transport core modules never import board modules.

See [WORKFLOWS.md](WORKFLOWS.md) for role-independent ownership, configurable review
policies, acceptance, reassignment, schema migration and recovery commands.

## Interfaces

- `team_assign`: register and durably dispatch; explicit activity, optional policy
  and parent_slice. Sender comes from runtime identity, never a default PM role.
- `team_inbox`: claim and parse commands; responses are receipts. Returns the
  assignment contract, message ID and lease token. Coordinate in Hermes; an
  execution activity may require a coding agent.
- `team_report`: submit actual outcome/evidence to the frozen return peer and
  required reviewers. Process exit alone is not an accepted delivery.
- `team_workflow`: describe, review, accept, reassign, reconcile.
- `team_bus`: inspect/status/log and legacy CRUD; cannot bypass new acceptance gates.

The lean envelope is `{"schema_version":"1.0","intent":"assign","slice":"S1"}`.
Intents remain assign/report_only/report/cancel. Bodies live in context files;
contracts and individual reviewer decisions live in SQLite. Legacy text PINGs and
unknown schema versions are rejected. Internal envelopes carry `delivery=internal`;
origin metadata never authorizes an early external broadcast.

RUNNING requires an actual job handle for contracted work. PAUSED preserves the
resumable session in its sidecar while clearing active execution. Cancel stops
queued dispatch; it refuses active jobs until their owner pauses/stops them.

## Instruction documents

`team_bus write_doc` retains the existing document-edit permissions and allowlist:
`~/.hermes/TEAM.md` supports replace/append/upsert_section;
`~/.hermes/team/STANDING.md` supports replace/append. Operational notes use
`team_bus log`. Assignment letters use context files. Handoff.md is not a
coordination surface. Changing role bindings does not change document permissions.
