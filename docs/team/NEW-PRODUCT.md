# New product / app walkthrough

Use this when the operator asks to set up a product. Ask every question in
order. Record the answers before creating profiles, team roots, or assignments.
Do not reuse another live deployment’s spool, `workflows.json`, peers, or
`TEAM.md` unless the operator explicitly chooses that after seeing the isolation
options.

This is not teammate provisioning. After isolation is chosen, provision peers
with [provision.md](../../skills/team-ops/references/provision.md). Shared policy
stays in this checkout; local files hold only the operator’s answers.

## Questions (ask; do not assume)

Ask all of these. If an answer is “unsure”, stop and wait — do not pick a default.

### Isolation

1. **Team root.** New `$HERMES_HOME/team` (or another named root), or share the
   existing live team directory?
2. **Spool and ledger.** New `BOT_COMS_SPOOL_ROOT` + `bus.sqlite`, or the
   existing spool/ledger? Split roots are the native hard-isolation knob;
   a shared spool is cheaper and relies on frozen assignment scope.
3. **Cross-talk.** If both this product and an existing one can run at once,
   must peers be unable to read the other product’s mail? If yes, do not share
   a spool.

### Identity

4. **Product id** (slug), checkout path, and GitHub/Origin remote if any.
5. **Product owner** (human + which Hermes peer is `owner_peer` for root work).
6. **Escalation route** and outward notify origin (Discord session, CLI, none).
7. **Authorized workspaces / `write_roots`** for coding-agent jobs. Name paths
   that must stay forbidden (do not inherit another product’s roots).

### Peers

8. **Peer set.** Reuse existing profiles with new `BOT_COMS_*` pointing at this
   root, create a dedicated peer set, or mix (say which peers are shared)?
9. **Capabilities and bindings** for this product only: who may coordinate,
   implement, design, review, accept. Adding a capability does not create a
   review gate — name required gates explicitly.
10. **Review policy.** Which capabilities must approve a submission before
    owner accept? Empty gates means owner accept only.

### Delivery

11. **Delivery sequence** for first work. Pick one (or a named hybrid) and
    name who dispatches children:
    - research / inventory only (design readback, codebase survey, no product edits)
    - coordinate-then-children (coordinator assigns thin slices; workers implement)
    - implement-first (only if scope, freeze, and acceptance bar are already
      locked — rare for a brand-new product)
    Also name: land cadence (small reviewed merges to main vs preview/flags),
    whether existing code may be scrapped, and whether assigner-owned
    Completion checklists (CHECKLISTS.md) are mandatory on every child.
12. **Acceptance bar** — what "done" means for a root assignment. Explicitly
    separate: independent review gates → accountable-owner accept →
    authorized commit/push/PR → CI green → merge to main → authorized deploy →
    live revision / parity check. Say who signs exceptions (human vs Hermes
    owner_peer) and whether local tests or agent exit may ever count as
    landed/deployed (default: no).
13. **What stays unauthorized** until the operator unshelves or names it.
    List repos, feature areas, deploys, live/prod data, personal genomes,
    local model inference, paid models, and any sibling product that must not
    be touched. "Shelved" means no dispatch into those write_roots — not merely
    a note in TEAM.md.

### Org / models

14. **Models / IQ tier** for each peer in this product’s set (new or reused).
    Name model + provider + effort from the operator’s model table (or say
    “leave as-is” for a reused profile). Do not clone another profile’s
    credentials, `.env`, MEMORY, or fact stores.
15. **Org-chart placement** (human-facing only; does not route mail): which
    seat owns coordination, which file gets the row, and what must stay out of
    the implementer org.
16. **Dedicated product coordinator?** Reuse an existing coordinator peer with
    product-scoped assignments, or provision a new one? Only choose a new
    coordinator if concurrent load would thrash the existing product (or
    isolation Q1–Q3 already forced a separate peer set that needs its own
    owner_peer).

## After answers exist

1. Write local `TEAM.md` / `USAGE.md` from `examples/team/` using only the
   answers. Do not copy shared policy into Hermes.
2. Create `workflows.json` and `bot-coms init-spool` for the chosen root and
   peer IDs.
3. Provision any new peers per [provision.md](../../skills/team-ops/references/provision.md);
   point `.env` at **this** spool and peer id.
4. Smoke-test one `activity="coordinate"` assignment with no product edits.
   Confirm frozen contract, return peer, owner, and that root accept is the
   only outward notify.
5. Record the product’s isolation choice in the local TEAM.md so later
   “just assign it” does not silently share a spool.

## Do not

- Default to another product’s live team root, peers, or `write_roots`.
- Clone Hermes profiles or copy MEMORY/USER/fact stores.
- Create a second kanban “for humans” that bots must keep in sync.
- Start product work in the same turn as unanswered isolation questions.
