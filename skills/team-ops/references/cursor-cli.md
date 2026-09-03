# Cursor CLI (SWE)

Machine: Jasons-iMac. Use **`cursor_screen`** tool (plugin: hermes-team-ops).

## One session

Investigate → plan → implement in **one** Cursor session (worktree, not main).
Do not ask/plan then respawn. UX `write` may use `~/projects` (`write_roots`).
`force` stays approval-gated for product execute.

Coordination (`team_assign`, `team_bus`, `team_report`) is Hermes-only — never
from inside Cursor. No synchronous `ask_team`.

## Mid-task questions

Cursor question / needs peer = **PAUSED**, not LANDED/FAIL/BLOCKED.
Hermes `team_assign`s the question async, keeps unblocked work, resumes the
**same** session with the answer. Unattended Cursor question prompts are
auto-skipped. Do not rely on them.

## After launch

End Hermes turn. Do not poll. `cursor_screen` runner EXIT calls
`bot-coms job-done <job>` (sidecar + `peers.yaml` → `team_report`).
No role allowlist — any roster profile reports on EXIT. No `wake-cli-job.sh`.

## Hard no's

- `agent ls` (TUI hangs)
- `pkill Edge`
- `--workspace ~/projects/open-genome` for product edits
- Resume chats `8b2c97d3` / `416a91a0`
- Verify job from a wake
- Print `.env` / tokens

## Binaries

- Cursor: `/Users/jason/.local/bin/agent`
- Hermes: `/Users/jason/.local/bin/hermes`

Worktree: `/Users/jason/.cursor/worktrees/open-genome/<name>`

Emergency shell wrapper: see historical `~/.hermes/CURSOR-CLI-PLAYBOOK.md` trap recipe.
