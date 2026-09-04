# Daily team prune

When invoked, inspect and clean active Hermes instructions and memories. Make only
evidence-backed maintenance changes. A clean run should make no changes.

## Sources of truth

Read these sources; reference them instead of copying their contents:

- [Profile rules](PROFILE-RULES.md): communication, memory ownership and storage.
- [Team policy](TEAM.md): shared constraints and its linked policy sources.
- Local `~/.hermes/TEAM.md` and `~/.hermes/team/USAGE.md`, when present: deployment-specific constraints.
- Local `~/.hermes/team/workflows.json`: current identities, capabilities and bindings.
- [Team operations](../../skills/team-ops/SKILL.md): coordination procedure.
- [Provisioning](../../skills/team-ops/references/provision.md): profile setup and templates.
- Each active profile's config and installed tools: actual runtime settings and interfaces.
- Each assignment's frozen contract: its participants, return path and review obligations.

## Inspect

1. Discover active profiles under `~/.hermes/profiles/`; do not assume a fixed roster.
   Exclude deleted profiles, backups, sessions, logs and generated task artifacts.
2. Read each profile's SOUL.md, MEMORY.md, USER.md and fact-store contents. Inspect
   enabled local and external skills, including references and symlink targets.
   Read databases without updating retrieval counters or exposing secret values.
3. Compare instructions with the sources above. Look for:
   - Reporting hierarchies or recipients embedded in prose instead of contracts.
   - Tool names, config keys, commands or paths contradicted by the installed runtime.
   - Task status, temporary paths, quota snapshots and incident workarounds stored as policy.
   - Shared rules copied into profiles, skills or multiple memory stores.
   - Personal facts in an unrelated profile; project specifications presented as universal preferences.
   - Unsupported certainty, redundant phrasing and instructions that encourage empty status updates.
4. Distinguish obsolete operating state from durable personal facts and historical
   subject matter. Age alone does not justify deletion. Do not invent corrections
   or research unrelated personal, medical or financial claims during this routine.
   Leave unresolved ambiguities unchanged and list them briefly in the result.

## Prune

1. Before changing anything, save affected files and SQLite backups under a new
   timestamped directory in `~/.hermes-backups/`. Keep it outside profile/skill
   discovery, restrict access, and record the affected paths and fact IDs there.
   Use SQLite's backup API for live databases. Stop mutations if backup fails.
2. Keep SOUL.md limited to the profile's responsibility, unique constraints and
   references to shared rules. Put each instruction in one authoritative source.
   Reusable rules belong in this repository; local files contain only deployment
   constraints and settings. Do not copy repo documents into Hermes.
   Remove duplicated or contradicted copies; do not replace them with historical
   explanations, deprecated-name lists or pointers to obsolete material.
3. Follow the profile rules for memory storage. Before emptying a startup memory
   file, preserve any unique, valid personal fact in its owning fact store.
   Verify that move before removing the source. Update an existing matching fact
   rather than inserting another copy. Never clear a database merely to reach zero.
4. Remove facts proven obsolete, misplaced duplicates and instruction copies.
   Preserve useful facts that do not violate the storage policy. For domain
   instructions, consolidate into the existing relevant skill and remove the copies.
5. Remove obsolete skill copies from active discovery only after confirming their
   necessary functionality is covered by the current source. Check that skill sync
   cannot silently restore them. Preserve unrelated skills and their update settings.
6. Use the installed memory store's mutation methods when they maintain all indexes.
   For direct SQL changes, inspect the current schema and use a transaction; maintain
   fact/entity links, full-text indexes and vector banks. Deleted facts must disappear
   from every retrieval path. Re-read before writing and stop that edit if another
   process changed the source since inspection.

## Boundaries

- Do not alter permissions, authentication, model choices, responsibility bindings,
  review policies or user authorization as a side effect of cleanup.
- Do not edit assignment history, queued work, session transcripts, project artifacts
  or backups. Do not resume tasks, launch agents, restart services or send messages.
- Do not install scheduling, commit, push or publish. This file defines the prune;
  the caller owns its schedule and any separately authorized actions.
- Do not append daily reports to instructions or store the cleanup itself as memory.

## Verify and report

- Re-scan active instructions and retrievable facts for the issues identified above.
- Check references resolve, required skills remain discoverable, and shared sources
  have no competing copies. Validate edited YAML/JSON and inspect the diff for
  unintended changes to runtime settings or permissions.
- Check database integrity, foreign keys, full-text consistency and vector-bank
  membership after mutations. Confirm removed facts cannot be retrieved.
- A second inspection should propose no further changes for the same findings.
- Return a short result: changed paths, facts removed/moved, checks, unresolved items
  and backup location. If clean, say “No prune needed.” Mention fresh-session loading
  only when changed instructions are already loaded in an open session.
