"""Durable messaging scheduler hosted by the existing Hermes dashboard backend.

SQLite owns admission and publication.  The plugin owns session bindings and
uses Hermes's supported CLI resume interface; there is no supervised worker.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import time
from pathlib import Path

from .config import allowed, load_config
from .store import Problem, Store
from . import turn_taking


def native_runtime(root):
    from bot_coms_runtime.cli_sessions import CliSessionRuntime
    return CliSessionRuntime(Path(root).parent.parent, 'bot-coms-messaging')


def default_selector_llm():
    """Resolve host PluginLlm from the bot-coms tools plugin when available."""
    try:
        from hermes_bot_coms import get_plugin_llm
        return get_plugin_llm()
    except ImportError:
        return None


class MessagingService:
    def __init__(self, root: Path, runtime, selector=None):
        self.root, self.runtime = Path(root), runtime
        self.store = Store(root)
        self.selector = selector
        self._leases = []
        self._task = None
        self._stopping = asyncio.Event()

    def acquire(self):
        """Fence other backends and refuse cutover while a legacy CLI holds a lease."""
        config = load_config(self.root)
        locks = self.root / 'locks'
        locks.mkdir(exist_ok=True, mode=0o700)
        paths = {locks / 'backend', *locks.iterdir(), *(locks / p['peer'] for p in config['profiles'])}
        try:
            for path in sorted(paths):
                if path.is_dir():
                    continue
                handle = path.open('a+b')
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    raise Problem(503, 'Another backend or legacy messaging execution is active')
                self._leases.append(handle)
            with self.store.db() as db:
                server = db.execute("SELECT value FROM meta WHERE key='executor_server_id'").fetchone()
                if server and server['value'] != config['server_id']:
                    raise Problem(503, 'Messaging storage belongs to a different server identity')
                db.execute("INSERT OR REPLACE INTO meta VALUES('executor_server_id',?)", (config['server_id'],))
                db.execute("INSERT OR REPLACE INTO meta VALUES('executor_mode','backend')")
                db.execute("DELETE FROM meta WHERE key='heartbeat'")
        except BaseException:
            self.release()
            raise

    def release(self):
        for handle in self._leases:
            handle.close()
        self._leases.clear()

    async def start(self):
        self.acquire()
        try:
            # Reconcile durable running admissions before accepting new turns.
            await self.tick()
            self._task = asyncio.create_task(self.run(), name='bot-coms-messaging')
        except BaseException:
            self.release()
            raise

    async def stop(self):
        self._stopping.set()
        if self._task:
            await self._task
        # The plugin owns admitted child processes. Shutdown fences/reconciles
        # them; it never repeats an uncertain operation.
        close = getattr(self.runtime, 'close', None)
        if close is not None:
            close(cancel=True)
        with self.store.db() as db:
            db.execute("DELETE FROM meta WHERE key='heartbeat'")
        self.release()

    async def run(self):
        while not self._stopping.is_set():
            try:
                await self.tick()
            except Exception:
                # No private runtime errors in public capabilities or transcripts.
                with self.store.db() as db:
                    db.execute("DELETE FROM meta WHERE key='heartbeat'")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=.25)
            except asyncio.TimeoutError:
                pass

    async def call(self, method, **kwargs):
        return await asyncio.to_thread(getattr(self.runtime, method), **kwargs)

    @staticmethod
    def operation(d):
        return 'messaging:' + d['id']

    def authorized(self, config, d):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM conversations WHERE id=?', (d['conversation'],)).fetchone()
        active = {p['id'] for p in allowed(config, row['owner'])}
        accessible = {p['id'] for p in config['profiles'] if row['owner'] in p['principals']}
        # A removed/revoked member must not leak shared conversation history.
        return (d['profile'] in active and d['profile'] in json.loads(row['profiles']) and
                set(json.loads(row['profiles'])).issubset(accessible))

    def _selector_llm(self):
        if self.selector is not None:
            return self.selector
        return default_selector_llm()

    async def _decide_empty_to(self, message, config):
        """Run or reuse turn-taking for a group empty-To user message."""
        mode = config.get('turn_taking_mode', 'on')
        input_seq = message['sequence']
        default = message['responder']
        cached = self.store.get_turn_decision(message['id'], input_seq)
        if cached is not None:
            return turn_taking.TurnDecision(
                action=cached['action'],
                speaker=cached['speaker'],
                reason=cached['reason'],
                model=cached['model'] or '',
                policy_version=cached['policy_version'],
                used_model=False,
            ), mode
        if mode == 'off':
            decision = turn_taking.TurnDecision(
                action='select',
                speaker=default,
                reason='ack',
                model='',
                used_model=False,
            )
            self.store.save_turn_decision(
                message['id'],
                input_seq=input_seq,
                policy_version=decision.policy_version,
                model=decision.model,
                action=decision.action,
                speaker=decision.speaker,
                reason=decision.reason,
                shadow=False,
            )
            return decision, mode
        members = json.loads(message['member_profiles'])
        member_ids = set(members)
        roster = [
            p for p in config['profiles']
            if isinstance(p, dict) and p.get('id') in member_ids
        ]
        if not roster:
            roster = [{'id': pid, 'name': pid, 'display_name': pid} for pid in members]
        with self.store.db() as db:
            history = [
                dict(r) for r in db.execute(
                    'SELECT id,sequence,author,body FROM messages WHERE conversation=? '
                    'ORDER BY sequence DESC LIMIT ?',
                    (message['conversation'], turn_taking.MAX_VIEW_MESSAGES),
                )
            ]
            history.reverse()
            wakes = db.execute(
                'SELECT count(*) FROM dispatches WHERE origin_message=?',
                (message['id'],),
            ).fetchone()[0]
        remaining = max(0, config['max_wakes_per_origin'] - wakes)
        decision = await turn_taking.select_speaker(
            self._selector_llm(),
            members=roster,
            member_ids=member_ids,
            default_responder=default,
            messages=history,
            unanswered_human=True,
            remaining_wakes=remaining,
        )
        self.store.save_turn_decision(
            message['id'],
            input_seq=input_seq,
            policy_version=decision.policy_version,
            model=decision.model,
            action=decision.action,
            speaker=decision.speaker,
            reason=decision.reason,
            shadow=(mode == 'shadow'),
        )
        return decision, mode

    async def resolve_pending_empty_to(self, config, busy_conversations: set):
        """Enqueue speakers for empty-To group messages before admit.

        Empty To persists with no dispatch. The selector chooses one member or
        yield; only failure / off / shadow wakes the default responder.
        Mutates busy_conversations for conversations already running; does not
        mark newly enqueued conversations so the admit loop can take them.
        """
        handled = set(busy_conversations)
        for message in self.store.pending_empty_to_messages():
            cid = message['conversation']
            if cid in handled:
                continue
            with self.store.db() as db:
                busy = db.execute(
                    """SELECT 1 FROM dispatches WHERE conversation=? AND
                       (state='running' OR (state='cancelled' AND binding_key IS NOT NULL AND runtime_ack=0))""",
                    (cid,),
                ).fetchone()
                latest_user = db.execute(
                    "SELECT coalesce(max(sequence),0) FROM messages WHERE conversation=? AND author='user'",
                    (cid,),
                ).fetchone()[0]
            if busy:
                handled.add(cid)
                continue
            if latest_user > message['sequence']:
                # Newer human message owns the conversation; record yield and move on.
                self.store.save_turn_decision(
                    message['id'],
                    input_seq=message['sequence'],
                    policy_version=turn_taking.POLICY_VERSION,
                    model='',
                    action='yield',
                    speaker=None,
                    reason='nothing_new',
                    shadow=False,
                )
                continue
            decision, mode = await self._decide_empty_to(message, config)
            if mode == 'shadow':
                self.store.enqueue_origin_dispatch(message['id'], message['responder'])
                handled.add(cid)
                continue
            if decision.action == 'yield':
                handled.add(cid)
                continue
            if decision.action == 'select' and decision.speaker:
                self.store.enqueue_origin_dispatch(message['id'], decision.speaker)
                handled.add(cid)
                continue
            self.store.enqueue_origin_dispatch(message['id'], message['responder'])
            handled.add(cid)

    async def apply_turn_taking(self, d, config):
        """Classify an ambiguous group wake before session admission.

        Empty-To selection normally happens in resolve_pending_empty_to. This
        path still handles cached decisions, shadow, and any legacy queued wakes.
        Returns an updated dispatch dict, or None when the turn was yielded.
        """
        mode = config.get('turn_taking_mode', 'on')
        if mode == 'off':
            return d
        with self.store.db() as db:
            conversation = db.execute(
                'SELECT * FROM conversations WHERE id=?', (d['conversation'],)
            ).fetchone()
            trigger = db.execute('SELECT * FROM messages WHERE id=?', (d['message'],)).fetchone()
        if conversation is None or trigger is None:
            return d
        recipients = json.loads(trigger['recipients'] or '[]')
        route = turn_taking.classify_route(
            conversation_kind=conversation['kind'],
            recipients=recipients,
            parent_dispatch=d.get('parent_dispatch'),
        )
        if route != 'ambiguous':
            return d
        input_seq = trigger['sequence']
        # Stale if a newer human message landed after this trigger was queued.
        with self.store.db() as db:
            latest_user = db.execute(
                "SELECT coalesce(max(sequence),0) FROM messages WHERE conversation=? AND author='user'",
                (d['conversation'],),
            ).fetchone()[0]
        if latest_user > input_seq:
            # A newer human message owns the conversation; yield this stale wake.
            if mode == 'on':
                self.store.yield_dispatch(d['id'])
                return None
            return d
        cached = self.store.get_turn_decision(d['message'], input_seq)
        if cached is not None:
            decision = turn_taking.TurnDecision(
                action=cached['action'],
                speaker=cached['speaker'],
                reason=cached['reason'],
                model=cached['model'] or '',
                policy_version=cached['policy_version'],
                used_model=False,
            )
        else:
            members = json.loads(conversation['profiles'])
            member_ids = set(members)
            roster = [
                p for p in config['profiles']
                if isinstance(p, dict) and p.get('id') in member_ids
            ]
            if not roster:
                roster = [{'id': pid, 'name': pid, 'display_name': pid} for pid in members]
            with self.store.db() as db:
                history = [
                    dict(r) for r in db.execute(
                        'SELECT id,sequence,author,body FROM messages WHERE conversation=? '
                        'ORDER BY sequence DESC LIMIT ?',
                        (d['conversation'], turn_taking.MAX_VIEW_MESSAGES),
                    )
                ]
                history.reverse()
                wakes = db.execute(
                    'SELECT count(*) FROM dispatches WHERE origin_message=?',
                    (d.get('origin_message') or d['message'],),
                ).fetchone()[0]
            remaining = max(0, config['max_wakes_per_origin'] - wakes)
            unanswered = trigger['author'] == 'user'
            decision = await turn_taking.select_speaker(
                self._selector_llm(),
                members=roster,
                member_ids=member_ids,
                default_responder=conversation['responder'],
                messages=history,
                unanswered_human=unanswered,
                remaining_wakes=remaining,
            )
            self.store.save_turn_decision(
                d['message'],
                input_seq=input_seq,
                policy_version=decision.policy_version,
                model=decision.model,
                action=decision.action,
                speaker=decision.speaker,
                reason=decision.reason,
                shadow=(mode == 'shadow'),
            )
        if mode == 'shadow':
            return d
        if decision.action == 'yield':
            self.store.yield_dispatch(d['id'])
            return None
        if decision.action == 'select' and decision.speaker and decision.speaker != d['profile']:
            if not self.store.retarget_dispatch(d['id'], decision.speaker):
                return d
            return {**d, 'profile': decision.speaker}
        return d

    async def settle(self, d, config):
        principal = d['owner']
        timed_out = d.get('admitted_at') is not None and time.time() - d['admitted_at'] >= config['run_timeout_seconds']
        if d['state'] == 'cancelled' or timed_out or not self.authorized(config, d):
            self.store.run_action(principal, d['id'], 'cancel')
            await self.call('cancel', principal_id=principal, operation_key=self.operation(d))
            receipt = await self.call('status', principal_id=principal, operation_key=self.operation(d))
            if receipt:
                self.record_delivery(d, receipt)
            # Cancellation is acknowledged only when the original native turn
            # actually drains, not merely when its receipt becomes cancelled.
            if not receipt or not receipt.get('active', receipt.get('status') == 'running'):
                with self.store.db() as db:
                    db.execute('UPDATE dispatches SET runtime_ack=1 WHERE id=?', (d['id'],))
            return
        receipt = await self.call('status', principal_id=principal, operation_key=self.operation(d))
        if receipt:
            self.record_delivery(d, receipt)
        if receipt and receipt['status'] == 'running':
            return
        current = load_config(self.root)
        if current['server_id'] != config['server_id'] or not self.authorized(current, d):
            self.store.run_action(principal, d['id'], 'cancel')
            await self.call('cancel', principal_id=principal, operation_key=self.operation(d))
            return
        result = receipt.get('result') if receipt else None
        body = result.get('text') if isinstance(result, dict) else result
        if receipt and receipt['status'] == 'completed' and isinstance(body, str) and 0 < len(body.encode('utf-8')) <= 1_000_000:
            self.store.finish(d['id'], body=body, profiles=allowed(current, principal),
                              max_mention_hops=current['max_mention_hops'],
                              max_wakes_per_origin=current['max_wakes_per_origin'])
        else:
            self.store.finish(d['id'], detail='Execution needs reconciliation; inspect the original Hermes session before continuing')
            if not receipt or receipt['status'] == 'indeterminate' or receipt.get('active', False):
                with self.store.db() as db:
                    db.execute('UPDATE session_bindings SET blocked=1 WHERE binding_key=?', (d['binding_key'],))

    def record_delivery(self, d, receipt):
        if receipt.get('not_admitted'):
            return
        with self.store.db() as db:
            binding = db.execute('SELECT session_id FROM session_bindings WHERE binding_key=?', (d['binding_key'],)).fetchone()
            session_id = receipt.get('session_id')
            if binding is None:
                raise RuntimeError('Missing plugin session binding')
            if binding['session_id'] and session_id and binding['session_id'] != session_id:
                raise RuntimeError('CLI runtime returned a different session identity')
            if session_id and not binding['session_id']:
                db.execute('UPDATE session_bindings SET session_id=? WHERE binding_key=?', (session_id, d['binding_key']))
            db.execute('INSERT OR IGNORE INTO session_messages SELECT ?,message FROM dispatch_context WHERE dispatch=?',
                       (d['binding_key'], d['id']))

    async def tick(self):
        try:
            config = load_config(self.root)
            with self.store.db() as db:
                server = db.execute("SELECT value FROM meta WHERE key='executor_server_id'").fetchone()
            if not server or server['value'] != config['server_id']:
                raise Problem(503, 'Messaging storage belongs to a different server identity')
        except Problem:
            with self.store.db() as db:
                active = [dict(r) for r in db.execute("SELECT d.*,c.owner FROM dispatches d JOIN conversations c ON c.id=d.conversation WHERE d.state='running'")]
                db.execute("DELETE FROM meta WHERE key='heartbeat'")
            for d in active:
                self.store.run_action(d['owner'], d['id'], 'cancel')
                await self.call('cancel', principal_id=d['owner'], operation_key=self.operation(d))
            return
        with self.store.db() as db:
            active = [dict(r) for r in db.execute("SELECT d.*,c.owner FROM dispatches d JOIN conversations c ON c.id=d.conversation WHERE d.state='running' OR (d.state='cancelled' AND d.binding_key IS NOT NULL AND d.runtime_ack=0)")]
        for d in active:
            await self.settle(d, config)
        running = {d['conversation'] for d in active}
        await self.resolve_pending_empty_to(config, running)
        with self.store.db() as db:
            queued = [dict(r) for r in db.execute("""SELECT d.*,c.owner,c.title FROM dispatches d
                JOIN conversations c ON c.id=d.conversation WHERE d.state='queued'
                AND NOT EXISTS (SELECT 1 FROM dispatches busy WHERE busy.conversation=d.conversation AND (busy.state='running' OR (busy.state='cancelled' AND busy.binding_key IS NOT NULL AND busy.runtime_ack=0)))
                ORDER BY d.created,d.id""")]
        # One turn per conversation per tick. Unrelated conversations can execute
        # concurrently in the native runtime without modifying profile globals.
        admitted = set()
        for d in queued:
            if d['conversation'] in admitted:
                continue
            if not self.authorized(config, d):
                self.store.run_action(d['owner'], d['id'], 'cancel')
                continue
            decided = await self.apply_turn_taking(d, config)
            if decided is None:
                admitted.add(d['conversation'])
                continue
            if not self.authorized(config, decided):
                self.store.run_action(d['owner'], d['id'], 'cancel')
                continue
            if await self.admit(decided, config):
                admitted.add(d['conversation'])
        with self.store.db() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat',?)", (str(time.time()),))

    async def admit(self, d, config):
        profile = next(p for p in config['profiles'] if p['id'] == d['profile'])
        key = hashlib.sha256(json.dumps([config['server_id'], d['owner'], d['conversation'], d['profile']], separators=(',', ':')).encode()).hexdigest()
        with self.store.db() as db:
            db.execute('INSERT OR IGNORE INTO session_bindings(binding_key,server_id,owner,conversation,profile,profile_name) VALUES(?,?,?,?,?,?)',
                       (key, config['server_id'], d['owner'], d['conversation'], d['profile'], profile['name']))
            binding = db.execute('SELECT * FROM session_bindings WHERE binding_key=?', (key,)).fetchone()
            if binding['blocked'] or binding['profile_name'] != profile['name']:
                return False
        session = await self.call('ensure_session', principal_id=d['owner'], profile=profile['name'], conversation_key=key, title=d['title'])
        with self.store.db() as db:
            if binding['session_id'] and session['session_id'] and binding['session_id'] != session['session_id']:
                db.execute('UPDATE session_bindings SET blocked=1 WHERE binding_key=?', (key,))
                return False
            if session['session_id']:
                db.execute('UPDATE session_bindings SET session_id=? WHERE binding_key=?', (session['session_id'], key))
            trigger = db.execute('SELECT sequence FROM messages WHERE id=?', (d['message'],)).fetchone()[0]
            conversation = db.execute('SELECT * FROM conversations WHERE id=?', (d['conversation'],)).fetchone()
            member_ids = set(json.loads(conversation['profiles']))
            context = [dict(r) for r in db.execute("""SELECT m.id,m.sequence,m.author,m.body FROM messages m
                WHERE m.conversation=? AND (m.sequence<=? OR m.author!='user')
                AND NOT EXISTS (SELECT 1 FROM session_messages s WHERE s.binding_key=? AND s.message=m.id)
                ORDER BY m.sequence""", (d['conversation'], trigger, key))]
            changed = db.execute("UPDATE dispatches SET state='running',binding_key=?,admitted_at=? WHERE id=? AND state='queued'", (key, time.time(), d['id'])).rowcount
            if not changed:
                return False
            db.executemany('INSERT OR IGNORE INTO dispatch_context VALUES(?,?)', [(d['id'], m['id']) for m in context])
        # Persist admission BEFORE crossing into the native runtime. A crash in
        # this window is ambiguous and must query its operation ledger, not replay.
        try:
            current = load_config(self.root)
            with self.store.db() as db:
                state = db.execute('SELECT state FROM dispatches WHERE id=?', (d['id'],)).fetchone()[0]
            if state != 'running' or current['server_id'] != config['server_id'] or not self.authorized(current, d):
                self.store.run_action(d['owner'], d['id'], 'cancel')
                return True
            roster = []
            for row in current['profiles']:
                if not isinstance(row, dict) or row.get('id') not in member_ids:
                    continue
                roster.append({
                    'id': row['id'],
                    'display_name': row.get('display_name') or row.get('name') or row['id'],
                })
            if not roster:
                roster = [{'id': pid, 'display_name': pid} for pid in sorted(member_ids)]
            payload = {'members': roster, 'messages': context}
            prompt = (
                'Respond to the latest addressed message in this persistent conversation. '
                'members lists every conversation bot with its stable id and display_name. '
                'messages[].author is user or a member id. '
                'Return your user-facing answer. For a useful handoff, mention exactly '
                'one member with @{id} from members (never nicknames or display names); '
                'the client shows the display name.\n\n'
                + json.dumps(payload, ensure_ascii=False)
            )
            receipt = await self.call('submit', principal_id=d['owner'], profile=profile['name'], conversation_key=key,
                            operation_key=self.operation(d), text=prompt, title=d['title'], max_turns=current['max_turns'])
            self.record_delivery({**d, 'binding_key': key}, receipt)
        except Exception as error:
            # This native exception is guaranteed to precede journal admission.
            # An absent operation receipt permits retrying a busy session; any
            # unknown admission failure remains ambiguous and is never replayed.
            receipt = await self.call('status', principal_id=d['owner'], operation_key=self.operation(d))
            if receipt is None:
                with self.store.db() as db:
                    db.execute("UPDATE dispatches SET state='queued',admitted_at=NULL WHERE id=? AND state='running'", (d['id'],))
            # A lost admission response might already have started tools. The next
            # tick consults status and never resubmits this operation.
            pass
        return True
