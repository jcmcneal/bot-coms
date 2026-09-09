# Backend-owned execution

This implementation requires only Hermes's public `hermes chat --resume`
interface. The dashboard plugin owns its binding and operation journals, then
starts bounded CLI turns inside its existing lifespan. No Hermes-core patch is
required. The transport-only library remains usable without Hermes or FastAPI.

## Ownership

The existing Hermes dashboard owns the plugin lifecycle; bot-coms owns messaging
and team scheduling, CLI session execution, and restart reconciliation. No
messaging, wake, or reconcile sidecar is installed.

SQLite remains durable: messaging stores messages, dispatches, session bindings,
and delivered context; the team board retains assignments, contracts, reviews,
and its outbox. `team/team-runtime.sqlite3` journals team turn admission. Hermes
stores exact session bindings and idempotent operation receipts in its own
plugin-session execution journal. Each store owns a different responsibility.

Messaging no longer publishes filesystem envelopes. The generic A2A spool keeps
its existing claim/ack protocol for interoperability and coding-agent callbacks.
Legacy files are preserved during migration, rather than discarded as cleanup.

## Install team delivery without messaging

In the Python environment serving Hermes:

```bash
python -m pip install -e '/path/to/bot-coms[backend]'
bot-coms install-dashboard --hermes-root /absolute/shared-hermes-root
```

Enable `bot-coms` in the shared instance, preserving existing plugin entries.
Participating profiles must also enable their appropriate tools/plugins:
`bot-coms` for generic A2A, plus `bot-coms-board` for contracted team work.
Existing `team/workflows.json`, `team/spool`, and profile identities remain valid.
The backend starts delivery when the team spool or workflow configuration exists.
Messaging remains optional and uses its own documented dashboard install command.

The backend scans for durable work from external processes, including coding-agent
EXIT callbacks. A missed notification therefore does not lose work. It checks
plugin enablement while running and cancels active team work on revocation.

## Cutover from standalone workers

1. Stop new dispatch admission and allow current work to drain, or explicitly
   cancel it. Record unfinished runs and coding-agent jobs before restarting.
2. Back up SQLite databases with SQLite's backup API, including committed WAL
   state. Preserve spool files, context files, and wake-state queries.
3. Install the matching Hermes and bot-coms implementations and dashboard assets.
   Stop and unload the old messaging launchd/systemd registration and any separate
   team wake/reconcile scheduling when it is safe to interrupt active work.
   Do not remove supervision of the existing Hermes backend itself.
4. Restart Hermes. The new services acquire exclusive ownership, refuse takeover
   while legacy execution holds a lease, and reconcile durable state. Legacy
   text-only wakes without a trustworthy matching envelope are retained for
   inspection, not guessed into a task session. Messaging schema v3 prevents old
   workers from claiming newly managed dispatches.
5. Verify a two-message DM reuses its session; verify a team assignment can be
   claimed, reported, reviewed, and accepted through the existing tools. Close
   the client during a turn and verify backend completion. Confirm the old
   service registrations are absent.

Do not replay indeterminate operations merely because the backend restarted.
Native operation keys let lost admission responses reconnect to known receipts.
If execution occurred without a durable receipt, inspect the original session
and external effects before continuing. Do not downgrade the migrated SQLite
schema or restore an older backup over new conversation history.

## Verification

Run bot-coms's unit, messaging, and smoke suites in an isolated environment.
The optional `tests/native` suite exercises real Hermes session handlers and
SQLite with a deterministic model stub; it makes no provider calls. Point Python
at both matching source trees (including Hermes's test fixtures):

```bash
PYTHONPATH=/path/to/hermes:/path/to/bot-coms/src:/path/to/bot-coms/adapters \
  /path/to/hermes/venv/bin/python /path/to/bot-coms/scripts/test-native-backend.py
```

These checks validate code integration; they do not establish live provider or
deployment health. Existing explicit/default routing and bounded mentions remain
the primary path. Predictive group speaker selection is available as a follow-on
admit-time aux call (see below).


## Turn-taking prediction

Before admitting an ambiguous group wake (user message with empty recipients,
so only the default responder was queued), the messaging backend may ask a
Hermes plugin auxiliary model which member should speak — or whether to yield.

Registration lives on the `bot-coms` tools plugin (`ctx.register_auxiliary_task`
for `bot_coms_turn_taking`). Messaging calls `ctx.llm.acomplete_structured`
through that task; it never constructs a second LLM client or starts `hermes chat`
for the selector.

Pin a cheap/fast model in Hermes `config.yaml`:

```yaml
auxiliary:
  bot_coms_turn_taking:
    provider: auto
    model: vendor/fast-small
    timeout: 8
```

Messaging `plugin-data/bot-coms-messaging/config.json` mode:

| `turn_taking_mode` | Behavior |
| --- | --- |
| `off` | Never call the selector (legacy admit path). |
| `shadow` | Call and persist the decision; still admit the original dispatch. |
| `on` (default) | Apply select / retarget / yield before session submit. |

DMs, explicit recipients, and `@mention` hops skip the selector. Team-board
assignment / review / acceptance never consults it. Unknown speaker ids and
timeouts fall back to the default responder when a human request is unanswered,
otherwise yield. Decisions are stored in SQLite `turn_decisions` and reused for
the same message sequence so a tick replay does not re-ask the model.


## Current integration boundaries

Tool approvals retain Hermes policy. The native receipt exposes pending requests,
but Conduit's v1 messaging view currently displays the run as running and has no
approval prompt. An authorized Hermes client must answer native approvals.
The plugin facade is for trusted local code; the messaging adapter authorizes
principals and profiles. Backend administrators can inspect native session history.

Task-local routing reaches local terminal processes and the matching
`hermes-team-ops` coding-agent launcher. Install that routing change along with
Hermes and bot-coms when using `agent_screen`. Remote terminal environments do not
yet receive this bridge, and isolated compute execution is rejected before native
admission. The existing Hermes backend must remain running for delivery.

Conduit speaking / waiting / yielded UI states and sequential “everyone’s views”
queues remain out of scope for this selector slice.
