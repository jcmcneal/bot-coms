# Role-independent team workflows

The engine follows assignment IDs and peer IDs, never job titles or reporting
lines. `workflows.json` selects responsibilities, capabilities and review gates.
`examples/workflows.json` expresses the current team: implementation requires
engineering review and independent verification, with product-owner acceptance.
Changing those bindings changes **new assignments only**.

## Configuration

Copy `examples/workflows.json` to `~/.hermes/team/workflows.json` and edit it.
`BOT_COMS_WORKFLOWS` may override the path. No external YAML dependency is needed.

- `peers`: stable peer IDs, Hermes `profile`, and `capabilities`. Profiles also
  route doorbells; `peers.yaml` remains a fallback for older installations.
- `bindings`: responsibility → peer ID. Titles and organizational managers are
  informational and never determine a task's return address.
- `policies`: optional owner responsibility, worker capability and required gates.
  Each gate has a unique ID, reviewer responsibility, capability and an
  `independent` boolean (default true). All gates must approve the same revision.
- `defaults`: activity → policy. The example defaults `implement` to `delivery`;
  `ui_delivery` additionally requires design review. Coordination and research
  can use their own policies. A named unknown policy fails before registration.

Keep peer IDs stable across title changes. Removing a reviewer from the directory
will not remove their obligations on existing assignments; transfer those explicitly.
The directory describes trusted local participants, not an OS security boundary.
All same-user processes can access the ledger; these checks prevent workflow
mistakes, not malicious local code with filesystem access.

## Assign and execute

`team_assign` accepts `activity` (`coordinate`, `implement`, `research`, `review`),
`policy`, and `parent_slice` in addition to the existing arguments. Default activity
is `coordinate`. A sender identity is required; it is never guessed to be PM.

```text
team_assign(slice="PARENT", to_peer="em", activity="coordinate", ...)
team_assign(slice="CHILD", to_peer="swe", activity="implement", parent_slice="PARENT", ...)
```

The frozen contract records the actual return peer, accountable owner, activity,
policy, gate reviewers, parent, assignment generation and submission revision.
Re-registering identical work preserves its contract and status. Changed content
or worker requires a new slice or explicit reassignment. Assignment bodies stay
in context files; spool envelopes remain lean.

On a wake, call `team_inbox`. `coordinate` means perform coordination in Hermes;
`launch_agent` means the explicit activity may require a coding agent. Keep one
resumable agent session per slice. After launch, stamp `RUNNING` with `active_job`,
then `bot_coms_ack` using the returned `message_id` and `lease_token`. Coordination
may acknowledge receipt and remain QUEUED until it has a result; do not invent an
agent handle for it. ACK responses are receipts, not new work or outward reports.

The runner records `EXECUTED`, `ASK_DONE`, `PLAN_DONE`, `ERROR` or `UNKNOWN`.
Exit 0 proves process completion only. The worker reads the result and calls
`team_report` with its actual outcome and evidence. A job asking a question is
`PAUSED`: seek peer guidance in Hermes and resume the same session. A stale job
cannot report over a newer active handle. Existing sidecar session IDs are retained.

## Review and acceptance

`team_workflow` is registered in the existing `team_bus` toolset. Its actor comes
from `BOT_COMS_PEER_ID`, not an actor argument supplied by the model.

```text
team_workflow(action="describe", slice="CHILD")
team_workflow(action="review", slice="CHILD", gate="engineering",
              revision=1, decision="APPROVED", evidence="Reviewed commit ...; checks ...")
team_workflow(action="review", slice="CHILD", gate="verification",
              revision=1, decision="APPROVED", evidence="Independent acceptance checks ...")
team_workflow(action="accept", slice="CHILD", revision=1,
              evidence="Accepted outcome ...; evidence ...; NEXT: ...")
```

Submissions notify the actual assigner, accountable owner and required reviewers.
Reviewers obtain independent evidence and submit APPROVED or REJECTED. Their
individual decisions are append-only, attributed and tied to a submission revision.
Rework creates a new revision and requires new approvals. Process exits alone do
not create a delivery submission. Raw `team_bus verdict/status DONE` cannot bypass
acceptance for contracted work.

Only the accountable owner accepts; every required gate and child assignment must
be accepted first. A child acceptance returns internally. A **root** acceptance
creates an external notification to its saved origin with the owner's outcome
summary and evidence. Internal reports and receipts never notify Discord/SPM.
Unresolved blockers remain inside the team until the owner deliberately escalates
through the existing outbound adapter; automatic completion reporting does not
invent an escalation decision.

## Change the team during work

```text
team_workflow(action="reassign", slice="CHILD", to_peer="new-builder",
              gate_peers={"engineering":"new-reviewer"},
              reason="Team responsibility transferred")
```

Only the current accountable owner can transfer work. `owner_peer` and
`return_peer` optionally transfer those responsibilities too. Stop or pause the
active execution first. The parent link and history remain; the generation and
revision advance, old approvals are invalidated, and old assignment envelopes are
acknowledged without launching work. Completed assignments are immutable.

## Recovery and operation

Board events and outbound delivery records commit in the same SQLite transaction.
The relay reuses a stable envelope ID after a crash, retries failed notifications,
and groups wake attempts by peer. Normal receipts do not wake an agent. Claims,
lease creation and reclamation share a process/thread lock; lease tokens fence
stale acknowledgements. Transport completion is persisted before result publication,
so a reclaimed Worker delivery can republish its saved result.

Run `bot-coms-board reconcile` from a scheduler every minute. It recovers completed
**contracted** jobs whose runner notification was missed, reclaims stale processing
leases for pending deliveries, and retries outstanding wakes. It does not create
coding-agent jobs, restart old threads, or rewrite historical slice ownership.
`scripts/workflow-reconcile.sh` is the scheduler entry point; install it into the
operator's existing scheduler rather than running another LLM polling loop.
External providers may receive a duplicate after a crash between successful send
and recording success; end-to-end exactly-once delivery requires provider support.

CLI equivalents are `bot-coms-board workflow describe|review|accept|reassign`.
Use `--peer`, `--slice`, `--revision`, `--gate`, `--decision`, `--evidence`,
`--to-peer`, `--owner-peer`, `--return-peer`, `--gate-peers` (JSON) and `--reason`.
`--team-root` and `--spool-root` support isolated deployments and tests.

Schema migration adds contracts, event history and a delivery outbox. Legacy rows
retain their existing ownership, status and jobs; gates are not retroactively
invented. Back up `bus.sqlite` using SQLite's backup API before rollout. Existing
Hermes processes need their plugin tools reloaded to expose `team_workflow`.
