# Cursor CLI (SWE)

Machine: Jasons-iMac. Use **`cursor_screen`** tool (plugin: hermes-team-ops).

## Stage gate

```
unknown → ask → plan → force (product worktree only)
```

Coordination: `team_bus` / `team_report` — never Cursor.

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
