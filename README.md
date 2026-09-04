# bot-coms

Standalone bot-to-bot messaging over a **local POSIX filesystem spool**. Delivery is at-least-once; completed handler results are replayable through the idempotency store. External side effects must be idempotent or transactionally coordinated; arbitrary effects are not guaranteed exactly once.

This project does **not** depend on the Hermes A2A gateway or Open Genome. HTTP transport is post-MVP (see `docs/HTTP_ADAPTER_SKETCH.md`).

**Team coordination** lives in the sibling package `bot_coms_board` (same repo): `bus.sqlite` ledger, `team_assign` / `team_inbox` / `team_report` Hermes tools, and `bot-coms-board` CLI. See `docs/BOARD.md` and [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) for configurable responsibilities, frozen ownership, independent review gates and owner acceptance. The transport core never imports the board lane.

MVP requires a **local POSIX** disk (APFS/ext4). NFS and other shared filesystems are unsupported.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

bot-coms init-spool --root /tmp/bot-coms-demo --peers a,b
echo '{"hello":"world"}' > /tmp/p.json
bot-coms send --root /tmp/bot-coms-demo --from a --to b --type request --payload-file /tmp/p.json
bot-coms claim --root /tmp/bot-coms-demo --peer b
# copy the id from the claim output, then:
bot-coms ack --root /tmp/bot-coms-demo --peer b --id <ID>
```

Library:

```python
from pathlib import Path
from bot_coms import Client, Worker, init_spool

root = Path("/tmp/bot-coms-demo")
init_spool(root, ["a", "b"])
a = Client(root, "a")
b = Client(root, "b")

env = a.send("b", "request", {"hello": "world"}, reply_to="a")
claimed = b.claim()
b.ack(claimed, result={"ok": True})
```

Handler contract: the transport may deliver the same logical message more than once. Before side effects, call through `Worker` (which gates on the idempotency store) or make the handler idempotent yourself.

## Tests

```bash
python -m pytest tests/unit tests/smoke -q
bot-coms smoke --all
```

## Hermes adapter

See `docs/INSTALL.md` and `adapters/hermes_bot_coms/README.md`. Enablement is a **user** action (`hermes plugins enable bot-coms`); this package does not rewrite Hermes config.

## Docs

- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — normative envelope, layout, and lifecycle
- [`docs/NON_GOALS.md`](docs/NON_GOALS.md)
- [`docs/INSTALL.md`](docs/INSTALL.md)
- [`docs/HTTP_ADAPTER_SKETCH.md`](docs/HTTP_ADAPTER_SKETCH.md) — post-MVP only
