# Operating loop

Canonical behavior is documented in `bot-coms/docs/WORKFLOWS.md`.

Requester → accountable owner → assignment → worker → independent gates →
owner acceptance → requester. Responsibilities are configured, not hardcoded titles.

`team_assign` saves an immutable ownership/policy snapshot. Use `parent_slice`
for child work and an explicit activity. On wake, `team_inbox` distinguishes
coordination from work needing an agent. Launch first, then stamp RUNNING with
active_job, then ACK using the message ID and lease token. End turn after dispatch.

Runner EXIT records execution state and wakes internal recipients. The worker
must read the outcome and submit evidence. Reviewers record separate decisions
against that submission revision. The owner accepts only after gates and children
pass. Only root acceptance generates an automatic outward completion message.

Questions are PAUSED; resolve through asynchronous Hermes assignments and resume
the same agent session. Org edits affect new assignments; explicit reassignment
transfers unfinished obligations without erasing history. Do not use Handoff.md.

`bot-coms-board reconcile` is deterministic recovery, suitable for a one-minute
scheduler. It retries durable notifications and recovers missed job EXIT hooks.
It never decides to launch a coding agent. Routine ACKs do not wake anyone.
