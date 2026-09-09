"""A server-owned worker. No model calls happen during import or API requests."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .config import allowed, load_config
from .store import Problem, Store


class Worker:
    def __init__(self, root: Path, execute=None):
        from bot_coms import Client, init_spool
        self.root, self.store = Path(root), Store(root)
        self.spool = self.root / 'spool'
        self._init_spool = init_spool
        self.client_type = Client
        self.execute = execute or self.execute_hermes
        (self.root / 'locks').mkdir(exist_ok=True, mode=0o700)
        (self.root / 'runs').mkdir(exist_ok=True, mode=0o700)
        self.config = self.refresh_config()
        self.sender = Client(self.spool, 'inbox')

    def refresh_config(self):
        """Reload config and ensure spool peers exist for newly enrolled profiles."""
        self.config = load_config(self.root)
        self._init_spool(self.spool, ['inbox'] + [p['peer'] for p in self.config['profiles']])
        return self.config

    def heartbeat(self):
        with self.store.db() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat', ?)", (str(time.time()),))

    def step(self, profile):
        # The Hermes child inherits this lease. A dead supervisor cannot double-launch the peer.
        with (self.root / 'locks' / profile['peer']).open('a+b') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            self.refresh_config()
            active_profile = next((p for p in self.config['profiles'] if p['id'] == profile['id'] and p.get('enabled')), None)
            if active_profile is None:
                return False
            peer = self.client_type(self.spool, profile['peer'])
            peer.reclaim_stale()
            # A crash after spool publication can leave an unreferenced duplicate. The SQL
            # dispatch owns launch identity; drain receipts without ever executing them.
            for envelope in peer.receive(limit=100):
                with self.store.db() as db:
                    existing = db.execute('SELECT state,envelope FROM dispatches WHERE id=?', (envelope.payload.get('dispatch_id'),)).fetchone()
                if existing is None or existing['state'] not in ('queued', 'running') or (existing['envelope'] and existing['envelope'] != envelope.id):
                    stale = peer.claim(envelope.id)
                    if stale is not None: peer.ack(stale)
            with self.store.db() as db:
                # A running row with no inherited lease owner is ambiguous, never replayed.
                db.execute("UPDATE dispatches SET state='needs_attention',detail='Worker interrupted; inspect the original run before retrying' WHERE profile=? AND state='running'", (profile['id'],))
                d = db.execute("SELECT * FROM dispatches WHERE profile=? AND state='queued' ORDER BY created,id LIMIT 1", (profile['id'],)).fetchone()
                if d is None:
                    return False
                d = dict(d)
                conversation = db.execute('SELECT * FROM conversations WHERE id=?', (d['conversation'],)).fetchone()
                authorized = {p['id'] for p in allowed(self.config, conversation['owner'])}
                if profile['id'] not in authorized or profile['id'] not in json.loads(conversation['profiles']):
                    db.execute("UPDATE dispatches SET state='cancelled',detail='Profile access changed' WHERE id=?", (d['id'],))
                    return True
                trigger = db.execute('SELECT sequence FROM messages WHERE id=?', (d['message'],)).fetchone()[0]
                messages = db.execute('SELECT author,body FROM messages WHERE conversation=? AND sequence<=? ORDER BY sequence DESC LIMIT 100', (d['conversation'], trigger)).fetchall()
                # Keep recent shared messages within a bounded context. Original history remains durable.
                context, remaining = [], 48000
                for m in messages:
                    body = m['body'][-remaining:]
                    context.append(dict(author=m['author'], body=body)); remaining -= len(body)
                    if remaining <= 0: break
                context.reverse()
            if d['envelope'] is None:
                env = self.sender.send(profile['peer'], 'event', {'dispatch_id': d['id']},
                                       idempotency_key=d['id'], correlation_id=d['conversation'])
                with self.store.db() as db:
                    db.execute('UPDATE dispatches SET envelope=? WHERE id=?', (env.id, d['id']))
                d['envelope'] = env.id
            peer = self.client_type(self.spool, profile['peer'])
            peer.reclaim_stale()
            claimed = peer.claim(d['envelope'])
            if claimed is None:
                return False
            with self.store.db() as db:
                changed = db.execute("UPDATE dispatches SET state='running' WHERE id=? AND state='queued'", (d['id'],)).rowcount
            if not changed:
                peer.ack(claimed)
                return True
            try:
                body = self.execute(active_profile, d, context, lease.fileno())
                # Recheck current server authorization as well as membership at publication.
                current = load_config(self.root)
                if profile['id'] not in {p['id'] for p in allowed(current, conversation['owner'])}:
                    self.store.run_action(conversation['owner'], d['id'], 'cancel')
                else:
                    self.store.finish(
                        d['id'],
                        body=body,
                        detail='' if body else 'Run produced no final reply; inspect the server run',
                        profiles=current.get('profiles') or [],
                        max_mention_hops=int(current.get('max_mention_hops', 2)),
                        max_wakes_per_origin=int(current.get('max_wakes_per_origin', 4)),
                    )
            except Exception:
                # Detailed execution logs stay private on the server, never in the public transcript.
                self.store.finish(d['id'], detail='Run interrupted or failed; inspect the server run before retrying')
            peer.ack(claimed)
            return True

    def execute_hermes(self, profile, dispatch, context, lease_fd):
        directory = self.root / 'runs' / dispatch['id']
        directory.mkdir(exist_ok=True, mode=0o700)
        query = directory / 'query.txt'
        query.write_text(
            'You are responding to a persistent shared conversation. Respond to the latest '
            'user or bot message addressed to you. Earlier messages are shared context, with '
            'their author IDs. You may hand off to another group member once by mentioning '
            'them with @name or @id (conversation members only). The server drops cycles and '
            'extra wakes. Do not @ everyone. Return your user-facing answer.\n\n'
            + json.dumps(context, ensure_ascii=False)
        )
        query.chmod(0o600)
        argv = [self.config['hermes_executable'], '-p', profile['name'], 'chat', '--in', '~', '-Q',
                '--max-turns', str(max(1, min(100, int(self.config.get('max_turns', 12))))), '--query-file', str(query)]
        # No --continue, --resume, YOLO, approval overrides, shell invocation, or CLI output scraping.
        env = os.environ.copy()
        # A worker started from a task must not inherit task-specific routing into the fresh run.
        for key in list(env):
            if key.startswith(('HERMES_KANBAN_', 'BOT_COMS_DEFAULT_SOURCE')):
                env.pop(key)
        timeout = max(5, min(3600, int(self.config.get('run_timeout_seconds', 600))))
        output, errors = directory / 'final.txt', directory / 'private.log'
        with output.open('w') as stdout, errors.open('w') as stderr:
            output.chmod(0o600); errors.chmod(0o600)
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       env=env, pass_fds=(lease_fd,), start_new_session=True)
            try:
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    with self.store.db() as db:
                        state = db.execute('SELECT state FROM dispatches WHERE id=?', (dispatch['id'],)).fetchone()[0]
                    config = load_config(self.root)
                    with self.store.db() as db:
                        owner = db.execute('SELECT owner FROM conversations WHERE id=?', (dispatch['conversation'],)).fetchone()[0]
                    authorized = profile['id'] in {p['id'] for p in allowed(config, owner)}
                    if state != 'running' or not authorized or time.monotonic() >= deadline or output.stat().st_size > 1_000_000 or errors.stat().st_size > 8_000_000:
                        raise RuntimeError('Run cancelled, revoked, exceeded output limit, or timed out')
                    time.sleep(.5)
                if process.returncode:
                    raise RuntimeError('Hermes run failed')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait()
        # Only quiet CLI stdout is public. stderr contains diagnostics and the runtime ID.
        return output.read_bytes()[:1_000_000].decode('utf-8', errors='ignore').strip()


def main():
    parser = argparse.ArgumentParser(description='Run the persistent bot-coms messaging worker')
    parser.add_argument('--root', required=True, type=Path, help='Messaging plugin-data directory containing config.json')
    args = parser.parse_args()
    worker = Worker(args.root)
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    active = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        while not stopping.is_set():
            try:
                config = worker.refresh_config()
                worker.heartbeat()
                for p in config['profiles']:
                    future = active.get(p['id'])
                    if p.get('enabled') and (future is None or future.done()):
                        if future is not None:
                            try: future.result()
                            except Exception: pass  # Durable rows remain recoverable; no private logs emitted.
                        active[p['id']] = pool.submit(worker.step, p)
            except Problem:
                pass  # Do not advertise fresh readiness while configuration is invalid.
            stopping.wait(1)
        # On graceful shutdown, drain active attempts; no new work is launched.
        with worker.store.db() as db:
            db.execute("DELETE FROM meta WHERE key='heartbeat'")


if __name__ == '__main__':
    main()
