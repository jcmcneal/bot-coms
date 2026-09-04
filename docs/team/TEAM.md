# Shared team policy

Read [profile rules](PROFILE-RULES.md), [delivery rules](DELIVERY.md) and the
[team-ops skill](../../skills/team-ops/SKILL.md). Local constraints live in
`~/.hermes/TEAM.md`; runtime settings and state stay in Hermes. See
[document ownership](README.md) for locations and precedence.

## Work constraints

- Product edits use `agent_screen` with the configured backend in an isolated
  worktree. Read the live source and settle the plan before editing. Do not
  self-authorize implementation, push, PR or merge work.
- Use disposable QA accounts and synthetic test data. Cite checks actually
  performed; local test results do not establish CI success.
- Experimental infrastructure changes use independent tools and deterministic
  smoke checks before adoption in the live coordination path.
- Finish authorized work. Try permitted alternatives after tool/path failures
  and back off on infrastructure errors. `resource_exhausted` is an infrastructure
  condition, not a product failure.
- Do not terminate unrelated browser processes or start another messaging gateway.

## Communication

Use 1–3 sentences for external updates: outcome, evidence and NEXT when relevant.
Send requested screenshots as native media. Say nothing when there is no new
information. Use “dry run” in new coordination language.

Escalate only for a user decision, an unresolved blocker after peer alternatives
fail, an accepted outcome that changes NEXT, or a safety stop. Use the locally
configured escalation route within the user's authorization. Routine status and
phase changes stay internal.

## Resource use

Use current meter output and the local budget policy. Pause only work consuming
an exhausted pool; other authorized work can continue. Keep usage snapshots out
of instructions and memory. Missing usage data is unknown, not zero usage.
