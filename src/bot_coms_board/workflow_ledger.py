"""Atomic workflow operations, mixed into BusStore.

Each decision is append-only and tied to a submission revision. Raw board CRUD
cannot accept a contracted assignment or change its ownership.
"""
from __future__ import annotations
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


class WorkflowLedger:
    @contextmanager
    def workflow_transaction(self):
        with self._lock:
            self._conn.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def _workflow_row(self, slice_id):
        row = self._conn.execute('SELECT * FROM slices WHERE id=?', (slice_id,)).fetchone()
        if row is None:
            raise ValueError(f'slice not found: {slice_id}')
        contract = json.loads(row['contract'] or '{}')
        if not contract:
            raise ValueError('legacy assignment: no workflow contract; register new work to use gates')
        return row, contract

    def _workflow_event(self, slice_id, kind, actor, details):
        from bot_coms.envelope import generate_ulid
        now = datetime.now(timezone.utc)
        cursor = self._conn.execute('INSERT INTO workflow_events(slice_id,kind,actor,details,at) VALUES (?,?,?,?,?)',
                           (slice_id, kind, actor, json.dumps(details), now.isoformat()))
        row, c = self._workflow_row(slice_id)
        intent = c.get('intent', 'assign') if kind == 'assigned' else 'assign' if kind == 'reassignment' else 'report'
        recipients = {row['peer']} if kind in ('assigned', 'reassignment') else {c['return_peer']}
        if kind == 'submission':
            recipients.update(g['peer'] for g in c['gates'])
            recipients.add(c['owner_peer'])
        if kind == 'execution':
            recipients.add(row['peer'])  # worker must classify actual output and resume if needed
        if kind == 'review':
            recipients.add(c['owner_peer'])
            if details['decision'] == 'REJECTED':
                recipients.add(row['peer'])
        key = f"{intent}:{slice_id}:{(row['content_sha256'] or 'none')[:8]}:{c['generation']}"
        if kind not in ('assigned', 'reassignment'):
            key += f":event-{cursor.lastrowid}"
        for recipient in recipients:
            if recipient == actor and kind not in ('assigned', 'execution', 'reassignment'):
                continue
            self._conn.execute('INSERT INTO workflow_outbox(message_id,event_id,sender,recipient,payload,headers,correlation_id,idem_key) VALUES (?,?,?,?,?,?,?,?)',
                (generate_ulid(now), cursor.lastrowid, actor, recipient,
                 json.dumps({'schema_version':'1.0','intent':intent,'slice':slice_id}),
                 json.dumps({'delivery':'internal', **({'source':row['notify_source']} if row['notify_source'] else {})}), slice_id, key))
        if kind == 'acceptance' and not c.get('parent_slice') and row['notify_source']:
            self._conn.execute('INSERT INTO workflow_outbox(message_id,event_id,sender,recipient,payload,headers,correlation_id,idem_key) VALUES (?,?,?,?,?,?,?,?)',
                (generate_ulid(now), cursor.lastrowid, actor, c['owner_peer'],
                 json.dumps({'intent':'ack','slice':slice_id,'verdict':'ACCEPTED','evidence':details['evidence']}),
                 json.dumps({'delivery':'external','source':row['notify_source']}), slice_id, key + ':external'))

    def pending_deliveries(self, *, ready_only=False):
        with self._lock:
            rows = [dict(r) for r in self._conn.execute('SELECT * FROM workflow_outbox WHERE delivered=0 OR (next_attempt>0 AND next_attempt<=?) ORDER BY event_id,message_id', (time.time(),))]
            return [r for r in rows if not ready_only or r['next_attempt'] <= time.time()]

    def delivery_finished(self, message_id, error=None, *, final=False):
        with self._lock, self._conn:
            self._conn.execute('UPDATE workflow_outbox SET delivered=?,last_error=?,next_attempt=? WHERE message_id=?',
                               (0 if error else 1, error, 0 if final else time.time()+60, message_id))

    def _save_contract(self, slice_id, contract, *, status=None, verdict=None, evidence=None):
        self._conn.execute('UPDATE slices SET contract=?, updated_at=? WHERE id=?',
                           (json.dumps(contract), _now(), slice_id))
        if status:
            self._conn.execute('UPDATE slices SET status=?, verdict=?, evidence=?, active_job=NULL WHERE id=?',
                               (status, verdict, evidence, slice_id))

    def workflow_history(self, slice_id):
        with self._lock:
            rows = self._conn.execute('SELECT * FROM workflow_events WHERE slice_id=? ORDER BY id', (slice_id,)).fetchall()
        return [{**dict(r), 'details': json.loads(r['details'])} for r in rows]

    def submit_work(self, slice_id, *, actor, verdict, evidence, job=None):
        if not evidence or not evidence.strip():
            raise ValueError('a submission requires evidence')
        with self.workflow_transaction():
            row, c = self._workflow_row(slice_id)
            if actor != row['peer']:
                raise ValueError('only the assigned worker can submit work')
            if row['status'] in ('DONE', 'CANCELLED'):
                raise ValueError('assignment is closed')
            if job and job != row['active_job']:
                raise ValueError('stale job completion')
            # Successful process exit is not a delivery submission.
            execution = verdict in ('EXECUTED', 'ASK_DONE', 'PLAN_DONE', 'UNKNOWN', 'ERROR', 'PAUSED')
            if not execution:
                if row['status'] == 'REVIEW' and c.get('submission') == {'verdict': verdict, 'evidence': evidence, 'actor': actor}:
                    return c
                c['revision'] += 1
                c['submission'] = {'verdict': verdict, 'evidence': evidence, 'actor': actor}
            status = 'PAUSED' if verdict == 'PAUSED' else 'BLOCKED' if verdict in ('FAIL', 'ERROR', 'UNKNOWN', 'BLOCKED') else 'REVIEW'
            self._save_contract(slice_id, c, status=status, verdict=verdict, evidence=evidence)
            self._workflow_event(slice_id, 'execution' if execution else 'submission', actor,
                                 {'revision': c['revision'], 'verdict': verdict, 'evidence': evidence, 'job': job})
        return c

    def record_review(self, slice_id, *, actor, gate, decision, evidence, revision):
        if decision not in ('APPROVED', 'REJECTED') or not evidence or not evidence.strip():
            raise ValueError('review requires APPROVED/REJECTED and evidence')
        with self.workflow_transaction():
            row, c = self._workflow_row(slice_id)
            if row['status'] in ('DONE', 'CANCELLED'):
                raise ValueError('assignment is closed')
            if not c['submission'] or revision != c['revision']:
                raise ValueError('review requires the current submitted revision')
            spec = next((g for g in c['gates'] if g['id'] == gate), None)
            if not spec or actor != spec['peer']:
                raise ValueError('actor is not the assigned gate reviewer')
            if spec['independent'] and actor == row['peer']:
                raise ValueError('worker cannot independently review its own work')
            self._workflow_event(slice_id, 'review', actor, {'gate': gate, 'decision': decision,
                                 'evidence': evidence, 'revision': revision})
            if decision == 'REJECTED':
                self._conn.execute("UPDATE slices SET status='BLOCKED', updated_at=? WHERE id=?", (_now(), slice_id))
        return self.get_slice(slice_id)

    def accept_work(self, slice_id, *, actor, evidence, revision):
        if not evidence or not evidence.strip():
            raise ValueError('acceptance requires an outcome summary and evidence')
        with self.workflow_transaction():
            row, c = self._workflow_row(slice_id)
            if actor != c['owner_peer']:
                raise ValueError('only the accountable owner can accept')
            if revision != c['revision'] or not c['submission']:
                raise ValueError('acceptance requires the current submitted revision')
            if row['status'] == 'DONE':
                return c
            if row['active_job'] or row['status'] != 'REVIEW':
                raise ValueError('work is not ready for acceptance')
            if c['submission']['verdict'] in ('FAIL', 'BLOCKED', 'REJECTED'):
                raise ValueError('failed submission cannot be accepted')
            reviews = {}
            for r in self._conn.execute("SELECT details FROM workflow_events WHERE slice_id=? AND kind='review' ORDER BY id", (slice_id,)):
                d = json.loads(r['details'])
                if d['revision'] == revision:
                    reviews[d['gate']] = d['decision']
            missing = [g['id'] for g in c['gates'] if reviews.get(g['id']) != 'APPROVED']
            if missing:
                raise ValueError(f'required reviews not approved: {missing}')
            for child in self._conn.execute('SELECT id,status,contract FROM slices'):
                cc = json.loads(child['contract'] or '{}')
                if cc.get('parent_slice') == slice_id and child['status'] != 'DONE':
                    raise ValueError(f'child assignment is not accepted: {child["id"]}')
            self._save_contract(slice_id, c, status='DONE', verdict='ACCEPTED', evidence=evidence)
            self._workflow_event(slice_id, 'acceptance', actor, {'revision': revision, 'evidence': evidence})
        return c

    def reassign_work(self, slice_id, *, actor, to_peer, to_profile, reason, owner_peer=None, return_peer=None, gate_peers=None):
        if not reason or not reason.strip():
            raise ValueError('reassignment requires a reason')
        with self.workflow_transaction():
            row, c = self._workflow_row(slice_id)
            if actor != c['owner_peer']:
                raise ValueError('only the accountable owner can reassign')
            if row['status'] in ('DONE', 'CANCELLED'):
                raise ValueError('closed work cannot be reassigned')
            if row['active_job']:
                raise ValueError('pause or finish the active job before reassignment')
            before = json.loads(json.dumps(c))
            for gate, peer in (gate_peers or {}).items():
                spec = next((g for g in c['gates'] if g['id'] == gate), None)
                if not spec:
                    raise ValueError(f'unknown gate: {gate}')
                spec['peer'] = peer
            if any(g['independent'] and g['peer'] == to_peer for g in c['gates']):
                raise ValueError('reassignment violates review independence')
            c['generation'] += 1
            c['revision'] += 1
            c['submission'] = None
            c['owner_peer'] = owner_peer or c['owner_peer']
            c['return_peer'] = return_peer or c['return_peer']
            self._save_contract(slice_id, c, status='QUEUED')
            self._conn.execute('UPDATE slices SET peer=?,to_profile=? WHERE id=?', (to_peer, to_profile, slice_id))
            self._workflow_event(slice_id, 'reassignment', actor,
                                 {'reason': reason, 'previous_worker': row['peer'], 'worker': to_peer, 'before': before, 'after': c})
        return self.get_slice(slice_id)
