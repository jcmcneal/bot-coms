# Team documents

Reusable instructions are maintained here and read directly from the installed
bot-coms checkout. Do not copy them into Hermes. Local files contain only the
operator's choices and runtime state.

| Source | Owns |
|---|---|
| [PROFILE-RULES.md](PROFILE-RULES.md) | Authorization, communication and memory storage |
| [TEAM.md](TEAM.md) | Shared work and escalation constraints |
| [CHECKLISTS.md](CHECKLISTS.md) | Assigner-created task checklists, delegation and verification |
| [DELIVERY.md](DELIVERY.md) | Delivery sequence |
| [PRUNE.md](PRUNE.md) | On-demand maintenance procedure |
| [team-ops skill](../../skills/team-ops/SKILL.md) | Assignment and review procedure |
| `~/.hermes/TEAM.md` | Local project constraints and escalation route |
| `~/.hermes/team/USAGE.md` | Local meter command and budget rules |
| `~/.hermes/team/STANDING.md`, if needed | Local delivery exceptions only |
| `~/.hermes/team/workflows.json` | Current profiles, capabilities, bindings and policies |
| Profile config, fact stores, board and spool | Runtime configuration, personal facts and task state |

Explicit user instructions take precedence. Local overrides apply to their named
scope; shared policy supplies the defaults. Existing assignments keep their frozen
contracts when local bindings change.

## Setup

Use the [provisioning guide](../../skills/team-ops/references/provision.md).
[Local TEAM template](../../examples/team/TEAM.md) and
[local USAGE template](../../examples/team/USAGE.md) contain fields to fill in,
not copies of shared policy. Keep only applicable local settings.

Team profiles read their local TEAM.md, which links to this checkout's TEAM.md.
Personal profiles read PROFILE-RULES.md directly using the checkout's absolute path.
Update these references if the checkout moves. Profile and posture templates hold
responsibilities only; do not repeat the operating procedure in them.

Invoke PRUNE.md directly when maintenance is wanted. It does not install a schedule.
