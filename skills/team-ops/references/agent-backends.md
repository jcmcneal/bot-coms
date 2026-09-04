# Coding-agent backends (SWE)

Machine: Jasons-iMac. Use **`agent_screen`** (plugin: hermes-team-ops).

## Backends

| backend | binary (typical) | notes |
|---|---|---|
| `cursor` (default) | `~/.local/bin/agent` | ask/plan/write/force map to Cursor flags |
| `codex` | `codex` | `exec` + sandbox / bypass |
| `claude_code` | `claude` | `-p` + stream-json |

Pick via launch arg `backend=` or profile `agent_screen.default_backend`.
Exact argv lives in hermes-team-ops test snapshots — do not invent flags here.

## One session

Investigate → plan → implement in **one** agent session (worktree, not main).
Do not ask/plan then respawn. UX `write` may use `~/projects` (`write_roots`).
`force` stays approval-gated for product execute.

Coordination (`team_assign`, `team_bus`, `team_report`) is Hermes-only — never
from inside the coding agent. No synchronous `ask_team`.

## Mid-task questions (PAUSED / resume)

Agent question / needs peer = **PAUSED**, not LANDED/FAIL/BLOCKED.
Hermes `team_assign`s the question async, keeps unblocked work, resumes the
**same** session with `session_id` from the sidecar on the next `agent_screen`
launch. Unattended in-agent question prompts are auto-skipped — do not rely on them.

## After launch

End Hermes turn. Do not poll. `agent_screen` runner EXIT calls
`bot-coms job-done <job>` (sidecar + `peers.yaml` → `team_report`).
No role allowlist — any roster profile reports on EXIT. No `wake-cli-job.sh`.

## Hard no's

- `agent ls` (TUI hangs)
- `pkill Edge`
- `--workspace ~/projects/open-genome` for product edits (use a worktree)
- Resume chats `8b2c97d3` / `416a91a0`
- Verify job from a wake
- Print `.env` / tokens

## Binaries

- Cursor: `/Users/jason/.local/bin/agent`
- Codex: `/usr/local/bin/codex`
- Claude Code: `/Users/jason/.local/bin/claude`
- Hermes: `/Users/jason/.local/bin/hermes`

Worktree: prefer an absolute existing worktree (not main). Cursor worktrees often
live under `~/.cursor/worktrees/<repo>/<name>`.
