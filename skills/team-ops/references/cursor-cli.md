# Cursor CLI (SWE)

Machine: Jasons-iMac. Use **`cursor_screen`** tool (plugin: hermes-team-ops).

## Stage gate

```
unknown → ask → plan → force (product worktree only)
```

Coordination: `team_bus` / `team_report` — never Cursor.

## After launch

End Hermes turn. Do not poll. Runner calls `wake-cli-job.sh` → `team_report`.

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
