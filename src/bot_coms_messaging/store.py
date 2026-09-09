from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .mentions import resolve_mentions


class Problem(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


def new_id() -> str:
    return str(uuid.uuid4())


_SCHEMA_V2 = '''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    dm_profile TEXT, title TEXT NOT NULL, profiles TEXT NOT NULL,
                    responder TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    updated REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0, muted INTEGER NOT NULL DEFAULT 0,
                    read_seq INTEGER NOT NULL DEFAULT 0, request_id TEXT,
                    UNIQUE(owner, dm_profile), UNIQUE(owner, request_id));
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation TEXT NOT NULL REFERENCES conversations(id),
                    sequence INTEGER NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL,
                    created REAL NOT NULL, client_id TEXT, recipients TEXT,
                    UNIQUE(conversation, sequence), UNIQUE(conversation, client_id));
                CREATE TABLE IF NOT EXISTS dispatches (
                    id TEXT PRIMARY KEY, conversation TEXT NOT NULL REFERENCES conversations(id),
                    message TEXT NOT NULL REFERENCES messages(id), profile TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', detail TEXT NOT NULL DEFAULT '',
                    envelope TEXT, created REAL NOT NULL,
                    parent_dispatch TEXT REFERENCES dispatches(id),
                    hop INTEGER NOT NULL DEFAULT 0,
                    origin_message TEXT REFERENCES messages(id),
                    UNIQUE(message, profile));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    conversation TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_history ON messages(conversation, sequence);
                CREATE INDEX IF NOT EXISTS dispatch_state ON dispatches(state, created);
                PRAGMA user_version = 2;
'''


class Store:
    """Short IMMEDIATE transactions serialize first-send, sequence, and outbox changes."""
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'messages.sqlite'
        with self.db() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 4:
                raise RuntimeError('Messaging database requires a newer adapter')
            if version == 0:
                db.executescript(_SCHEMA_V2)
            elif version == 1:
                db.executescript('''
                    ALTER TABLE dispatches ADD COLUMN parent_dispatch TEXT REFERENCES dispatches(id);
                    ALTER TABLE dispatches ADD COLUMN hop INTEGER NOT NULL DEFAULT 0;
                    ALTER TABLE dispatches ADD COLUMN origin_message TEXT REFERENCES messages(id);
                    UPDATE dispatches SET origin_message = message WHERE origin_message IS NULL;
                    PRAGMA user_version = 2;
                ''')
            if version < 3:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS session_bindings (
                        binding_key TEXT PRIMARY KEY, server_id TEXT NOT NULL,
                        owner TEXT NOT NULL, conversation TEXT NOT NULL REFERENCES conversations(id),
                        profile TEXT NOT NULL, profile_name TEXT NOT NULL, session_id TEXT,
                        blocked INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(server_id,owner,conversation,profile));
                    CREATE TABLE IF NOT EXISTS session_messages (
                        binding_key TEXT NOT NULL REFERENCES session_bindings(binding_key),
                        message TEXT NOT NULL REFERENCES messages(id), PRIMARY KEY(binding_key,message));
                    CREATE TABLE IF NOT EXISTS dispatch_context (
                        dispatch TEXT NOT NULL REFERENCES dispatches(id), message TEXT NOT NULL REFERENCES messages(id),
                        PRIMARY KEY(dispatch,message));
                    ALTER TABLE dispatches ADD COLUMN binding_key TEXT REFERENCES session_bindings(binding_key);
                    ALTER TABLE dispatches ADD COLUMN admitted_at REAL;
                    ALTER TABLE dispatches ADD COLUMN runtime_ack INTEGER NOT NULL DEFAULT 0;
                    CREATE TRIGGER backend_executor_fence BEFORE UPDATE OF state ON dispatches
                    WHEN NEW.state='running' AND (SELECT value FROM meta WHERE key='executor_mode')='backend'
                    BEGIN SELECT CASE WHEN messaging_backend_connection()!=1 THEN RAISE(ABORT,'Backend executor required') END; END;
                    PRAGMA user_version = 3;
                """)
            if version < 4:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS turn_decisions (
                        message TEXT PRIMARY KEY REFERENCES messages(id),
                        input_seq INTEGER NOT NULL,
                        policy_version TEXT NOT NULL,
                        model TEXT NOT NULL DEFAULT '',
                        action TEXT NOT NULL,
                        speaker TEXT,
                        reason TEXT NOT NULL,
                        shadow INTEGER NOT NULL DEFAULT 0,
                        created REAL NOT NULL);
                    PRAGMA user_version = 4;
                """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.create_function("messaging_backend_connection", 0, lambda: 1)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _row(self, db, owner, cid):
        row = db.execute('SELECT * FROM conversations WHERE id=? AND owner=?', (cid, owner)).fetchone()
        if row is None:
            raise Problem(404, 'Conversation not found')
        return row

    def _event(self, db, row, kind):
        db.execute('INSERT INTO events(owner, conversation, kind, created) VALUES(?,?,?,?)',
                   (row['owner'], row['id'], kind, time.time()))

    def _summary(self, db, row):
        latest = db.execute('SELECT body FROM messages WHERE conversation=? ORDER BY sequence DESC LIMIT 1', (row['id'],)).fetchone()
        unread = db.execute("SELECT count(*) FROM messages WHERE conversation=? AND sequence>? AND author!='user'",
                            (row['id'], row['read_seq'])).fetchone()[0]
        return dict(id=row['id'], kind=row['kind'], title=row['title'], profiles=json.loads(row['profiles']),
                    default_responder=row['responder'], revision=row['revision'], preview=latest['body'][:180] if latest else '',
                    updated_at=row['updated'], unread=unread, archived=bool(row['archived']), pinned=bool(row['pinned']), muted=bool(row['muted']))

    @staticmethod
    def _message(row):
        return dict(id=row['id'], sequence=row['sequence'], author=row['author'], body=row['body'], created_at=row['created'])

    def dm_id(self, owner, profile):
        with self.db() as db:
            row = db.execute('SELECT id FROM conversations WHERE owner=? AND dm_profile=?', (owner, profile)).fetchone()
            if row is None:
                raise Problem(404, 'No DM yet')
            return row['id']

    def groups(self, owner, title, profiles, responder, request_id):
        if not title.strip() or len(title) > 120 or len(set(profiles)) < 2 or len(profiles) > 12 or responder not in profiles:
            raise Problem(422, 'Choose a name, at least two bots, and a default responder')
        with self.db() as db:
            old = db.execute('SELECT * FROM conversations WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
            if old:
                if old['title'] != title.strip() or set(json.loads(old['profiles'])) != set(profiles) or old['responder'] != responder:
                    raise Problem(409, 'This request ID was already used for a different group')
                return self._summary(db, old)
            cid = new_id()
            db.execute('INSERT INTO conversations(id,owner,kind,title,profiles,responder,updated,request_id) VALUES(?,?,?,?,?,?,?,?)',
                       (cid, owner, 'group', title.strip(), json.dumps(sorted(set(profiles))), responder, time.time(), request_id))
            row = self._row(db, owner, cid)
            self._event(db, row, 'conversation.created')
            return self._summary(db, row)

    def send(self, owner, client_id, body, recipients, *, cid=None, dm=None, revision=None):
        if not client_id or len(client_id) > 128 or not body.strip() or len(body) > 32000:
            raise Problem(422, 'Message must contain between 1 and 32000 characters and a client ID')
        with self.db() as db:
            if dm:
                profile, display = dm
                row = db.execute('SELECT * FROM conversations WHERE owner=? AND dm_profile=?', (owner, profile)).fetchone()
                if row is None:
                    cid = new_id()
                    db.execute('INSERT INTO conversations(id,owner,kind,dm_profile,title,profiles,responder,updated) VALUES(?,?,?,?,?,?,?,?)',
                               (cid, owner, 'dm', profile, display, json.dumps([profile]), profile, time.time()))
                    row = self._row(db, owner, cid)
                cid = row['id']
            else:
                row = self._row(db, owner, cid)
            old = db.execute('SELECT * FROM messages WHERE conversation=? AND client_id=?', (cid, client_id)).fetchone()
            if old:
                if old['body'] != body or json.loads(old['recipients']) != sorted(set(recipients)):
                    raise Problem(409, 'This message ID was already used with different content')
                return dict(conversation=self._summary(db, row), message=self._message(old))
            if row['archived']:
                raise Problem(409, 'Reopen this conversation before sending')
            if revision is not None and revision != row['revision']:
                raise Problem(409, 'Membership changed; refresh before sending')
            targets = sorted(set(recipients)) or [row['responder']]
            if not set(targets).issubset(json.loads(row['profiles'])):
                raise Problem(403, 'A recipient is not a conversation member')
            mid = self._append(db, row, 'user', body, client_id, json.dumps(sorted(set(recipients))))
            for profile in targets:
                db.execute(
                    'INSERT INTO dispatches(id,conversation,message,profile,created,parent_dispatch,hop,origin_message) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (new_id(), cid, mid, profile, time.time(), None, 0, mid))
            return dict(conversation=self._summary(db, self._row(db, owner, cid)),
                        message=self._message(db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()))

    def _append(self, db, row, author, body, client_id=None, recipients=None):
        sequence = db.execute('SELECT coalesce(max(sequence),0)+1 FROM messages WHERE conversation=?', (row['id'],)).fetchone()[0]
        mid, now = new_id(), time.time()
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (mid, row['id'], sequence, author, body, now, client_id, recipients))
        db.execute('UPDATE conversations SET updated=? WHERE id=?', (now, row['id']))
        self._event(db, row, 'message.created')
        return mid

    def receipt(self, owner, cid, client_id):
        with self.db() as db:
            row = self._row(db, owner, cid)
            message = db.execute('SELECT * FROM messages WHERE conversation=? AND client_id=?', (cid, client_id)).fetchone()
            if message is None:
                raise Problem(404, 'Message not accepted')
            return dict(conversation=self._summary(db, row), message=self._message(message))

    def conversations(self, owner, after='', limit=100):
        with self.db() as db:
            rows = db.execute('SELECT * FROM conversations WHERE owner=? AND id>? ORDER BY id LIMIT ?', (owner, after, limit + 1)).fetchall()
            return dict(conversations=[self._summary(db, r) for r in rows[:limit]], cursor=rows[limit-1]['id'] if len(rows) > limit else None)

    def history(self, owner, cid, before=None):
        with self.db() as db:
            row = self._row(db, owner, cid)
            messages = db.execute('SELECT * FROM messages WHERE conversation=? AND sequence<? ORDER BY sequence DESC LIMIT 101',
                                  (cid, before or 9223372036854775807))
            page, size, more = [], 0, False
            for message in messages:
                encoded_size = len(json.dumps(self._message(message), ensure_ascii=False).encode('utf-8'))
                if page and (len(page) == 100 or size + encoded_size > 1_200_000):
                    more = True
                    break
                page.append(message)
                size += encoded_size
            runs = db.execute('SELECT id,profile,state AS status,detail FROM dispatches WHERE conversation=? ORDER BY created DESC LIMIT 50', (cid,)).fetchall()
            return dict(conversation=self._summary(db, row), messages=[self._message(m) for m in reversed(page)],
                        runs=[dict(r) for r in runs], before=page[-1]['sequence'] if more else None)

    def read(self, owner, cid, sequence):
        with self.db() as db:
            row = self._row(db, owner, cid)
            maximum = db.execute('SELECT coalesce(max(sequence),0) FROM messages WHERE conversation=?', (cid,)).fetchone()[0]
            sequence = max(row['read_seq'], min(maximum, max(0, sequence)))
            db.execute('UPDATE conversations SET read_seq=? WHERE id=?', (sequence, cid))
            return dict(sequence=sequence)

    def user_state(self, owner, cid, values):
        if not values or not set(values).issubset({'archived', 'pinned', 'muted'}) or any(type(v) is not bool for v in values.values()):
            raise Problem(422, 'Invalid inbox state')
        with self.db() as db:
            self._row(db, owner, cid)
            for field, value in values.items():
                db.execute(f'UPDATE conversations SET {field}=? WHERE id=?', (int(value), cid))
            row = self._row(db, owner, cid)
            self._event(db, row, 'conversation.updated')
            return self._summary(db, row)

    def update_group(self, owner, cid, revision, title, profiles, responder):
        if len(set(profiles)) < 2 or len(profiles) > 12 or responder not in profiles or not title.strip() or len(title) > 120:
            raise Problem(422, 'Invalid group members or title')
        with self.db() as db:
            row = self._row(db, owner, cid)
            if row['kind'] != 'group' or row['revision'] != revision:
                raise Problem(409, 'Refresh the group before editing')
            db.execute('UPDATE conversations SET title=?,profiles=?,responder=?,revision=revision+1 WHERE id=?',
                       (title.strip(), json.dumps(sorted(set(profiles))), responder, cid))
            removed = set(json.loads(row['profiles'])) - set(profiles)
            for profile in removed:
                db.execute("UPDATE dispatches SET state='cancelled',detail='Member removed' WHERE conversation=? AND profile=? AND state IN ('queued','running')", (cid, profile))
            row = self._row(db, owner, cid)
            self._event(db, row, 'membership.changed')
            return self._summary(db, row)

    def run_action(self, owner, run, action):
        with self.db() as db:
            d = db.execute('SELECT d.* FROM dispatches d JOIN conversations c ON c.id=d.conversation WHERE d.id=? AND c.owner=?', (run, owner)).fetchone()
            if d is None:
                raise Problem(404, 'Run not found')
            if action == 'cancel' and d['state'] in ('queued', 'running'):
                db.execute("UPDATE dispatches SET state='cancelled', detail='Cancelled by user' WHERE id=?", (run,))
            elif action == 'retry':
                # Side effects may have happened. V1 deliberately never auto-replays ambiguous execution.
                raise Problem(409, 'Review the result and send a new instruction; this run may have performed actions')
            return dict(ok=True)

    def finish(self, dispatch, body=None, detail='', *, profiles=None,
               max_mention_hops=2, max_wakes_per_origin=4):
        """Complete a run. Bot bodies may enqueue hop-capped @ handoffs.

        User-body @ routing is never applied here — only resolved mentions in
        the bot reply against conversation members, subject to hop/budget caps.
        """
        with self.db() as db:
            d = db.execute('SELECT * FROM dispatches WHERE id=?', (dispatch,)).fetchone()
            if d is None or d['state'] != 'running':
                return False
            row = db.execute('SELECT * FROM conversations WHERE id=?', (d['conversation'],)).fetchone()
            members = json.loads(row['profiles'])
            if d['profile'] not in members:
                return False
            bot_mid = None
            if body:
                bot_mid = self._append(db, row, d['profile'], body)
            db.execute('UPDATE dispatches SET state=?,detail=? WHERE id=?',
                       ('completed' if body else 'needs_attention', detail, dispatch))
            if body and d['binding_key']:
                # Persist exactly the supplied messages and native final reply together.
                # Sequence alone cannot track replies interleaved with pending user sends.
                db.execute('INSERT OR IGNORE INTO session_messages SELECT ?,message FROM dispatch_context WHERE dispatch=?',
                           (d['binding_key'], dispatch))
                db.execute('INSERT OR IGNORE INTO session_messages VALUES(?,?)', (d['binding_key'], bot_mid))
            self._event(db, row, 'run.updated')
            if bot_mid and body:
                self._enqueue_bot_mentions(
                    db, row, d, bot_mid, body, profiles or [],
                    max_mention_hops=max_mention_hops,
                    max_wakes_per_origin=max_wakes_per_origin,
                )
            return True

    def _chain_profiles(self, db, dispatch_id) -> set[str]:
        seen: set[str] = set()
        current = dispatch_id
        while current:
            row = db.execute('SELECT profile, parent_dispatch FROM dispatches WHERE id=?', (current,)).fetchone()
            if row is None:
                break
            seen.add(row['profile'])
            current = row['parent_dispatch']
        return seen

    def _enqueue_bot_mentions(self, db, conversation, parent, bot_mid, body, profiles,
                              *, max_mention_hops, max_wakes_per_origin):
        members = set(json.loads(conversation['profiles']))
        roster = [p for p in profiles if isinstance(p, dict) and p.get('id') in members]
        if not roster:
            roster = [{'id': pid, 'name': pid, 'display_name': pid} for pid in members]
        targets = resolve_mentions(body, roster)
        parent_hop = int(parent['hop'] or 0)
        if parent_hop >= max_mention_hops:
            return
        origin = parent['origin_message'] or parent['message']
        wakes = db.execute(
            'SELECT count(*) FROM dispatches WHERE origin_message=?', (origin,)
        ).fetchone()[0]
        chain = self._chain_profiles(db, parent['id'])
        next_hop = parent_hop + 1
        for profile in targets:
            if profile == parent['profile'] or profile not in members or profile in chain:
                continue
            if wakes >= max_wakes_per_origin:
                break
            existing = db.execute(
                'SELECT id FROM dispatches WHERE message=? AND profile=?', (bot_mid, profile)
            ).fetchone()
            if existing is not None:
                continue
            db.execute(
                'INSERT INTO dispatches(id,conversation,message,profile,created,parent_dispatch,hop,origin_message) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (new_id(), conversation['id'], bot_mid, profile, time.time(),
                 parent['id'], next_hop, origin))
            wakes += 1

    def get_turn_decision(self, message_id: str, input_seq: int):
        """Return a persisted decision for message+sequence, or None if stale/missing."""
        with self.db() as db:
            row = db.execute(
                'SELECT * FROM turn_decisions WHERE message=? AND input_seq=?',
                (message_id, input_seq),
            ).fetchone()
            return dict(row) if row else None

    def save_turn_decision(
        self,
        message_id: str,
        *,
        input_seq: int,
        policy_version: str,
        model: str,
        action: str,
        speaker: str | None,
        reason: str,
        shadow: bool,
    ):
        with self.db() as db:
            db.execute(
                '''INSERT INTO turn_decisions(
                    message,input_seq,policy_version,model,action,speaker,reason,shadow,created)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(message) DO UPDATE SET
                    input_seq=excluded.input_seq,
                    policy_version=excluded.policy_version,
                    model=excluded.model,
                    action=excluded.action,
                    speaker=excluded.speaker,
                    reason=excluded.reason,
                    shadow=excluded.shadow,
                    created=excluded.created''',
                (
                    message_id,
                    input_seq,
                    policy_version,
                    model or '',
                    action,
                    speaker,
                    reason,
                    int(shadow),
                    time.time(),
                ),
            )

    def retarget_dispatch(self, dispatch_id: str, profile: str) -> bool:
        """Move a queued dispatch to another member. Fails if the target already has a row."""
        with self.db() as db:
            d = db.execute('SELECT * FROM dispatches WHERE id=? AND state=?', (dispatch_id, 'queued')).fetchone()
            if d is None:
                return False
            if d['profile'] == profile:
                return True
            clash = db.execute(
                'SELECT id FROM dispatches WHERE message=? AND profile=?',
                (d['message'], profile),
            ).fetchone()
            if clash is not None:
                return False
            changed = db.execute(
                "UPDATE dispatches SET profile=? WHERE id=? AND state='queued'",
                (profile, dispatch_id),
            ).rowcount
            return bool(changed)

    def yield_dispatch(self, dispatch_id: str) -> bool:
        """Complete a queued dispatch without a public reply or agent run."""
        with self.db() as db:
            d = db.execute('SELECT * FROM dispatches WHERE id=? AND state=?', (dispatch_id, 'queued')).fetchone()
            if d is None:
                return False
            row = db.execute('SELECT * FROM conversations WHERE id=?', (d['conversation'],)).fetchone()
            db.execute(
                "UPDATE dispatches SET state='completed', detail='yielded' WHERE id=? AND state='queued'",
                (dispatch_id,),
            )
            if row is not None:
                self._event(db, row, 'run.updated')
            return True

    def events(self, owner, after):
        with self.db() as db:
            rows = db.execute('SELECT * FROM events WHERE owner=? AND sequence>? ORDER BY sequence LIMIT 200', (owner, after)).fetchall()
            return dict(events=[dict(r) for r in rows], cursor=rows[-1]['sequence'] if rows else after)
