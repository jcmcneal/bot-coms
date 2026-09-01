# Non-goals (MVP)

`bot_coms` core is a local POSIX filesystem-spool messenger. The following are **out of scope** for the core package:

- HTTP / WebSocket transport (sketch only: `HTTP_ADAPTER_SKETCH.md`)
- BUS / slice ledger integration in the core import graph
- Hermes A2A gateway reuse, repair, or patches
- Multi-host clustering
- Auth federation, mTLS, or message encryption beyond filesystem modes
- GUI
- Any Hermes gateway / config mutation performed by this repo
- NFS / network filesystems as the spool
- Group-writable multi-user spools (single OS user owns the tree)
- Cross-message transactions and global ordering

**Sibling lane:** `bot_coms_board` (same repo, separate package) owns
`bus.sqlite` coordination, high-level assign/inbox/report tools, and the
Hermes `bot-coms-board` plugin. The core never imports `bot_coms_board`; an
import-boundary test enforces this.
