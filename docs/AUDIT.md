# Quick operational audit

Use this after an upgrade or when checking a running Hermes team. Start with
read-only inspection; report transport health separately from task progress and
unrelated gateway integrations. Record the time, commit, deployment paths, and
checks actually performed. A clean queue alone does not prove end-to-end health.

## 1. Identify the deployment and installed code

Run from the repository root. Set these paths for the deployment being audited:

```sh
export AUDIT_TEAM_ROOT="${BOT_COMS_TEAM_ROOT:-$HOME/.hermes/team}"
export AUDIT_SPOOL_ROOT="${BOT_COMS_SPOOL_ROOT:-$AUDIT_TEAM_ROOT/spool}"
export AUDIT_HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
git status --short --branch
git log -1 --oneline
"$AUDIT_HERMES_PY" - <<'PY'
import importlib.metadata as metadata
from pathlib import Path
import bot_coms, bot_coms_board
import hermes_bot_coms, hermes_bot_coms_board

for module in (bot_coms, bot_coms_board, hermes_bot_coms, hermes_bot_coms_board):
    print(module.__name__, module.__file__)
for entry in metadata.distribution('bot-coms').entry_points:
    if entry.group == 'hermes_agent.plugins':
        print('plugin', entry.name, entry.value)
for package in (hermes_bot_coms, hermes_bot_coms_board):
    for source in (Path('adapters') / package.__name__).glob('*.py'):
        installed = Path(package.__file__).parent / source.name
        print(source, 'MATCH' if installed.read_bytes() == source.read_bytes() else 'DIFF')
PY
```

Expected: both plugin entry points exist and installed adapters match the intended
release. Editable core imports can point at this repository while adapters remain
copied into site-packages; check both. Fresh imports do not prove that an already
running gateway has reloaded its modules.

For each participant in `workflows.json`, inspect only the relevant profile
settings: both plugins enabled, `team_bus` and `bot_coms` toolsets exposed, and
`BOT_COMS_PEER_ID` / `BOT_COMS_SPOOL_ROOT` matching the workflow and spool. Do not
dump whole `.env` files or credentials into audit output.

## 2. Inspect the ledger and spool without consuming messages

This snapshot opens SQLite in read-only mode and prints metadata, not task bodies.
Counts can change while bots work; correlate anomalies by message/slice ID.

```sh
"$AUDIT_HERMES_PY" - <<'PY'
import json, os, sqlite3, time
from pathlib import Path
from datetime import datetime, timezone

team = Path(os.environ['AUDIT_TEAM_ROOT']).expanduser().resolve()
spool = Path(os.environ['AUDIT_SPOOL_ROOT']).expanduser()
print('Audit UTC:', datetime.now(timezone.utc).isoformat())
with sqlite3.connect((team / 'bus.sqlite').as_uri() + '?mode=ro', uri=True) as db:
    db.row_factory = sqlite3.Row
    queries = {
        'integrity': 'PRAGMA quick_check',
        'statuses': 'SELECT status, count(*) AS n FROM slices GROUP BY status',
        'recent slices': '''SELECT id, peer, status, active_job, updated_at
                           FROM slices ORDER BY updated_at DESC LIMIT 12''',
        'outbox': '''SELECT delivered, count(*) AS n FROM workflow_outbox
                     GROUP BY delivered''',
        'pending or retrying': '''SELECT message_id, correlation_id, recipient,
                                 delivered, next_attempt, last_error
                                 FROM workflow_outbox
                                 WHERE delivered=0 OR next_attempt>0
                                 ORDER BY event_id DESC LIMIT 20''',
        'recent events': '''SELECT id, slice_id, kind, actor, at
                           FROM workflow_events ORDER BY id DESC LIMIT 15''',
    }
    for label, query in queries.items():
        print(label, json.dumps([dict(row) for row in db.execute(query)], indent=2))
for peer in sorted(spool.iterdir()):
    if not peer.is_dir():
        continue
    for bucket in ('inbox', 'processing', 'acked', 'dead-letter'):
        files = list((peer / bucket).glob('*.json'))
        print(peer.name, bucket, 'count', len(files))
        if bucket == 'acked':
            continue
        for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            try:
                data = json.loads(path.read_text())
                envelope = data.get('envelope', data)
                payload = envelope.get('payload', {})
                payload = payload if isinstance(payload, dict) else {}
                print(json.dumps({
                    'id': path.stem,
                    'age_minutes': round((time.time() - path.stat().st_mtime) / 60, 1),
                    'type': envelope.get('type'), 'intent': payload.get('intent'),
                    'slice': payload.get('slice'), 'attempt': envelope.get('attempt'),
                    'reason': data.get('reason'), 'moved_at': data.get('moved_at'),
                }))
            except (OSError, ValueError) as exc:
                print(path.name, type(exc).__name__, str(exc))
PY
```

Interpretation:

- Integrity should return `ok`. Pending deliveries should clear after normal
  activity and applicable retry backoff. `delivered=1` means transport delivery,
  not acceptance; rows with a future `next_attempt` can still be monitored/retried.
- Distinguish actual assignments/reports from terminal ACK receipts. A few idle
  receipts are not a work backlog. Growing repeated receipt chains are a concern.
- For processing messages, inspect `state/leases/<message-id>.json` and the peer's
  lease settings. Old file modification time alone does not establish an expired
  lease; an active worker may be heartbeating.
- Compare dead-letter `moved_at` with the upgrade time. Historical dead letters
  are not new failures. Do not delete or replay them as part of a routine audit.
- Correlate a recent assignment with its acknowledged envelope and subsequent
  workflow events. QUEUED after a receipt may be coordination in progress;
  RUNNING requires a real job handle. REVIEW with execution evidence but no
  submission is not accepted work. Inspect the frozen contract and review gates.

## 3. Verify recovery expectations

`team_inbox` and `bot-coms-board inbox` reclaim the checking peer's stale leases,
then run one reconciliation pass before listing messages. Reconciliation recovers
missed exits for RUNNING contracted jobs, retries due deliveries/wakes, and
reclaims stale recipient leases encountered during those retries. It does not
restart coding jobs or turn execution evidence into owner acceptance.

No launchd/cron recovery job is required if agents keep checking their inboxes.
An idle team waits until the next check. A stale `workflow-reconcile.log` or an
absent scheduler is therefore **not a failure by itself**. Existing retry backoff
still applies; repeated inbox checks do not force retries early.

Inspect normal inbox tool results for `reclaimed`, `reconciliation.jobs`,
`reconciliation.pending`, and `reconciliation.error`. Top-level `success: true`
does not imply recovery was error-free: available inbox work can proceed after a
reconciliation error. Job entries can also contain individual errors.

Do not call the live inbox just to inspect it: it claims/acknowledges messages and
can trigger delivery and wakes. Explicit `bot-coms-board reconcile` also mutates
state and can deliver pending notifications. Use these only when recovery is in
scope; otherwise observe the agents' next normal check. Recovery on inbox reads
applies to the board API, not every low-level transport read.

## 4. Check code and gateway health

```sh
.venv/bin/python -m pytest tests/unit tests/smoke -q
hermes gateway status
```

If tests fail importing a newer adapter API, compare installed adapters first.
`PYTHONPATH=src:adapters .venv/bin/python -m pytest tests/unit tests/smoke -q`
tests current source explicitly; passing that command does not establish that the
installed plugin is current. Use isolated test spools, not synthetic messages sent
to working bots. Source tests do not prove a live handoff succeeded.

Inspect recent gateway logs around the relevant assignment or restart. Keep
filesystem-spool errors separate from A2A retries or duplicate Discord/Home
Assistant credentials. Bot-coms does not depend on A2A. Verify a new process and
startup completion after a requested restart; a restart request alone is not proof.

Avoid restarting, reinstalling, editing the ledger, sending messages, or accepting
work during an audit unless that action is also requested.

## 5. Report the result

Use this compact format; replace every placeholder with observed evidence:

```text
Audited <time/timezone>, commit <sha>, runtime <path>.
Transport: <pending delivery count, processing/lease findings, recent handoff>.
Recovery: <observed inbox reconciliation result, or unverified>.
Workflow: <queued/running/review state and any missing submission/acceptance>.
Validation: <exact test result, installed adapter match, gateway status>.
Concerns: <current issues; identify historical/unrelated noise separately>.
Changes made: <none, or exact authorized actions>.
```

See [workflow recovery](WORKFLOWS.md) and [installation](INSTALL.md) for details.
