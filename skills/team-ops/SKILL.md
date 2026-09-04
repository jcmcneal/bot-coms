---
name: team-ops
description: "Hermes team coordination — role-independent assignments, evidence, reviews and acceptance."
version: 2.0.0
platforms: [macos]
tags: [hermes, team, bot-coms, board]
---

# Team operations

Read `~/.hermes/TEAM.md`. Contracts: `docs/BOARD.md` and `docs/WORKFLOWS.md`
in this repository. Team configuration is `~/.hermes/team/workflows.json`.

## Dispatch

1. Write an assignment letter under `~/.hermes/team/context/` with outcome,
   acceptance criteria, constraints and evidence requirements.
2. Call `team_assign` with the explicit activity: coordinate, implement, research
   or review. Select a policy when the default does not fit. For delegated work,
   set `parent_slice` to the assignment you are delivering.
3. End the turn after dispatch. No synchronous waits or completion polling.

## Receive

1. Call `team_inbox`; inspect the frozen contract and activity.
2. Coordinate assignments in Hermes. Launch a coding agent only when the explicit
   implementation, research or review task needs one. Role titles do not decide.
3. For a launched job, stamp `team_bus status RUNNING` with its real `active_job`,
   then `bot_coms_ack` with `message_id` and `lease_token` from the inbox decision.
4. Keep one resumable agent session per slice; end the Hermes turn after launch.

## Completion and review

- Runner EXIT automatically records execution completion. It does not prove
  delivery or acceptance. Read the actual output before reporting a conclusion.
- Call `team_report` with the outcome and concrete evidence. Internal reports go
  to the actual assigner and required reviewers, never directly to the origin.
- Use `team_workflow describe` to inspect revision, owner, reviewers and history.
- Assigned reviewers use `team_workflow review` with gate, revision,
  APPROVED/REJECTED and independent evidence. Rework requires fresh reviews.
- The accountable owner uses `team_workflow accept` only after all required
  reviews and child assignments pass. Root acceptance reports the owner's short
  outcome/evidence/NEXT summary to the saved origin. Child acceptance stays internal.
- Routine ACKs, stage transitions and RUNNING are quiet. Escalate only a genuine
  decision or unresolved blocker after peer/fallback routes have failed.

## Questions and changing the team

An agent question is PAUSED. Hermes assigns the question asynchronously, continues
unblocked work, then resumes the same session using its saved session ID. Coding
agents never call `team_assign`. Do not respawn merely to change phases.

Editing workflows.json affects new assignments only. Use `team_workflow reassign`
with a reason to transfer unfinished work or reviewers. Only the accountable
owner can transfer; pause active execution first. History and parent links remain.

## Limits

- Spool + SQLite are the coordination surfaces. Do not fold or write Handoff.md.
- Never infer a task's return address from ORG.md, a role title or today's bindings.
- Keep bodies in context files and metadata in the ledger, not spool payloads.
- Do not infer LANDED from exit 0, missing output, screenshots from an older run,
  or an earlier review. Evidence must support the current submission.
- Product edits follow the team's worktree and coding-agent requirements.
- Never print credentials or `.ping-*` secrets.
- TEAM.md and STANDING.md writes use `team_bus write_doc` under existing permissions.

See `references/operating-loop.md` and `docs/WORKFLOWS.md` for commands and recovery.
