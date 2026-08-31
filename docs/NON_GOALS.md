# Non-goals (MVP)

bot-coms is a local POSIX filesystem-spool messenger. The following are **out of scope** for 0.1.0:

- HTTP / WebSocket transport (sketch only: `HTTP_ADAPTER_SKETCH.md`)
- BUS.md integration or reads/writes
- Hermes A2A gateway reuse, repair, or patches
- Multi-host clustering
- Auth federation, mTLS, or message encryption beyond filesystem modes
- GUI
- Any Hermes gateway / config mutation performed by this repo
- NFS / network filesystems as the spool
- Group-writable multi-user spools (single OS user owns the tree)
- Cross-message transactions and global ordering

Callers may optionally consult BUS for *their* coordination. bot-coms never takes a BUS dependency.
