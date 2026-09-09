import asyncio
import json
import sqlite3
import fcntl
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot_coms_messaging.api import create_router
from bot_coms_messaging.service import MessagingService
from bot_coms_messaging.store import Store, Problem
from tests.messaging.test_messaging import root


class Runtime:
    def __init__(self):
        self.sessions = {}
        self.operations = {}
        self.calls = []
        self.cancelled = []
        self.lose_response = False

    def ensure_session(self, **args):
        key = (args['principal_id'], args['profile'], args['conversation_key'])
        return self.sessions.setdefault(key, {'session_id': f'session-{len(self.sessions)}'})

    def submit(self, **args):
        key = args['operation_key']
        self.calls.append(args)
        receipt = self.operations.setdefault(key, dict(status='running', result=None, **self.ensure_session(**args)))
        if self.lose_response:
            raise ConnectionError('lost after native admission')
        return receipt

    def status(self, *, principal_id, operation_key):
        return self.operations.get(operation_key)

    def cancel(self, *, principal_id, operation_key):
        self.cancelled.append(operation_key)
        if operation_key in self.operations:
            self.operations[operation_key]['status'] = 'cancelled'

    def finish(self, body='reply'):
        for operation in self.operations.values():
            if operation['status'] == 'running':
                operation.update(status='completed', result=body)


def send(store, mid='first', body='hello', owner='test:alice', profile='swe-id'):
    return store.send(owner, mid, body, [], dm=(profile, profile))['conversation']['id']


def tick(service):
    asyncio.run(service.tick())


def service(root, runtime=None):
    result = MessagingService(root, runtime or Runtime())
    result.acquire()
    return result


def test_two_sends_reuse_session_and_send_only_unseen_context(root):
    runtime = Runtime()
    backend = service(root, runtime)
    try:
        cid = send(backend.store)
        tick(backend)
        runtime.finish('first answer')
        tick(backend)
        send(backend.store, 'second', 'follow up')
        tick(backend)
        assert len(runtime.sessions) == 1
        assert len(runtime.calls) == 2
        assert 'hello' in runtime.calls[0]['text']
        assert 'follow up' in runtime.calls[1]['text']
        assert 'hello' not in runtime.calls[1]['text']
        assert 'first answer' not in runtime.calls[1]['text']
        runtime.finish('second answer')
        tick(backend)
        assert len(backend.store.history('test:alice', cid)['messages']) == 4
        assert not (root / 'spool').exists()
    finally:
        backend.release()


def test_queued_user_messages_are_not_skipped_by_interleaved_replies(root):
    backend = service(root)
    try:
        send(backend.store, 'one', 'first question')
        send(backend.store, 'two', 'second question')
        tick(backend)
        assert len(backend.runtime.calls) == 1
        backend.runtime.finish('first answer')
        tick(backend)
        assert len(backend.runtime.calls) == 2
        text = backend.runtime.calls[1]['text']
        assert 'second question' in text and 'first question' not in text
    finally:
        backend.release()


def test_duplicate_send_and_lost_admission_response_reconcile_once(root):
    runtime = Runtime()
    runtime.lose_response = True
    backend = service(root, runtime)
    cid = send(backend.store)
    send(backend.store)
    tick(backend)
    backend.release()
    runtime.finish('durable result')
    reopened = service(root, runtime)
    try:
        tick(reopened)
        tick(reopened)
        assert len(runtime.calls) == 1
        assert len(reopened.store.history('test:alice', cid)['messages']) == 2
    finally:
        reopened.release()


def test_ambiguous_admission_is_not_replayed_and_blocks_session(root):
    backend = service(root)
    try:
        cid = send(backend.store)
        tick(backend)
        backend.runtime.operations.clear()  # native ledger missing after interrupted start
        tick(backend)
        send(backend.store, 'second')
        tick(backend)
        assert len(backend.runtime.calls) == 1
        statuses = [run['status'] for run in backend.store.history('test:alice', cid)['runs']]
        assert set(statuses) == {'queued', 'needs_attention'}
    finally:
        backend.release()


@pytest.mark.parametrize('change', ['cancel', 'revoke', 'disable'])
def test_cancellation_revocation_and_plugin_disable_stop_publication(root, change):
    backend = service(root)
    try:
        cid = send(backend.store)
        tick(backend)
        if change == 'cancel':
            with backend.store.db() as db:
                run = db.execute('SELECT id FROM dispatches').fetchone()[0]
            backend.store.run_action('test:alice', run, 'cancel')
        elif change == 'revoke':
            config = json.loads((root / 'config.json').read_text())
            config['profiles'][0]['principals'] = []
            (root / 'config.json').write_text(json.dumps(config))
        else:
            (root.parent.parent / 'config.yaml').write_text('{"plugins":{"enabled":[]}}')
        backend.runtime.finish('must not publish')
        tick(backend)
        assert backend.runtime.cancelled
        assert len(backend.store.history('test:alice', cid)['messages']) == 1
    finally:
        backend.release()


def test_session_isolation_and_replaced_server_fails_closed(root):
    backend = service(root)
    try:
        config = json.loads((root / 'config.json').read_text())
        for p in config['profiles']:
            p['principals'].append('test:bob')
        (root / 'config.json').write_text(json.dumps(config))
        send(backend.store)
        send(backend.store, owner='test:bob')
        send(backend.store, profile='designer-id')
        group = backend.store.groups('test:alice', 'Group', ['swe-id', 'designer-id'], 'swe-id', 'g')
        backend.store.send('test:alice', 'group-message', 'hello', [], cid=group['id'])
        tick(backend)
        assert len(backend.runtime.sessions) == 4
        backend.runtime.finish()
        tick(backend)
        config['server_id'] = 'replacement-server'
        (root / 'config.json').write_text(json.dumps(config))
        send(backend.store, 'new-server')
        tick(backend)
        assert len(backend.runtime.sessions) == 4
        with backend.store.db() as db:
            assert db.execute("SELECT * FROM meta WHERE key='heartbeat'").fetchone() is None
    finally:
        backend.release()


def test_backend_lease_and_old_connection_fence(root):
    backend = service(root)
    try:
        send(backend.store)
        with pytest.raises(Problem):
            service(root)
        legacy = sqlite3.connect(backend.store.path)
        try:
            with pytest.raises(sqlite3.OperationalError, match='messaging_backend_connection'):
                legacy.execute("UPDATE dispatches SET state='running'")
        finally:
            legacy.close()
    finally:
        backend.release()


def test_active_legacy_execution_prevents_cutover(root):
    locks = root / 'locks'
    locks.mkdir()
    with (locks / 'swe').open('a+b') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Problem, match='legacy'):
            service(root)


def test_router_lifespan_runs_backend_and_clears_readiness(root):
    runtime = Runtime()
    app = FastAPI()
    @app.middleware('http')
    async def auth(request, next):
        request.state.session = SimpleNamespace(provider='test', user_id='alice', org_id=None)
        return await next(request)
    app.include_router(create_router(lambda: root, runtime_factory=lambda: runtime))
    with TestClient(app) as client:
        assert client.get('/v1/capabilities').json()['state'] == 'ready'
        assert client.post('/v1/dms/swe-id/messages', params={'expected_server': 'test-server', 'expected_principal': 'test:alice'}, json={'client_message_id': 'lifespan', 'body': 'hello'}).status_code == 200
        # Native admission happens on the next scheduled backend pass.
        import time
        deadline = time.monotonic() + 2
        while not runtime.calls and time.monotonic() < deadline:
            time.sleep(.02)
        assert len(runtime.calls) == 1
    with Store(root).db() as db:
        assert db.execute("SELECT * FROM meta WHERE key='heartbeat'").fetchone() is None


def test_legacy_worker_command_refuses():
    from bot_coms_messaging.worker import main
    with pytest.raises(SystemExit, match='retired'):
        main()


def test_cancel_waits_for_native_turn_to_drain_before_next_admission(root):
    backend = service(root)
    try:
        send(backend.store)
        tick(backend)
        operation = next(iter(backend.runtime.operations.values()))
        operation['active'] = True
        with backend.store.db() as db:
            run = db.execute('SELECT id FROM dispatches').fetchone()[0]
        backend.store.run_action('test:alice', run, 'cancel')
        send(backend.store, 'next', 'new instruction')
        tick(backend)
        assert len(backend.runtime.calls) == 1
        operation['active'] = False
        tick(backend)
        assert len(backend.runtime.calls) == 2
        assert 'hello' not in backend.runtime.calls[1]['text']
    finally:
        backend.release()


def test_restart_after_publication_does_not_append_again(root):
    runtime = Runtime()
    backend = service(root, runtime)
    cid = send(backend.store)
    tick(backend)
    runtime.finish()
    tick(backend)
    backend.release()
    reopened = service(root, runtime)
    try:
        tick(reopened)
        assert len(runtime.calls) == 1
        assert len(reopened.store.history('test:alice', cid)['messages']) == 2
    finally:
        reopened.release()


def test_timeout_cancels_native_operation(root):
    backend = service(root)
    try:
        send(backend.store)
        tick(backend)
        with backend.store.db() as db:
            db.execute('UPDATE dispatches SET admitted_at=0')
        tick(backend)
        assert backend.runtime.cancelled
        with backend.store.db() as db:
            assert db.execute('SELECT state FROM dispatches').fetchone()[0] == 'cancelled'
    finally:
        backend.release()


@pytest.mark.parametrize('instance', ['plugins: [broken', '{"plugins":{"enabled":"bot-coms bot-coms-messaging"}}', '{"plugins":{"enabled":["bot-coms","bot-coms-messaging"],"disabled":"bot-coms"}}'])
def test_malformed_plugin_enablement_cancels_active_run(root, instance):
    backend = service(root)
    try:
        cid = send(backend.store)
        tick(backend)
        (root.parent.parent / 'config.yaml').write_text(instance)
        tick(backend)
        assert backend.runtime.cancelled
        assert backend.store.history('test:alice', cid)['runs'][0]['status'] == 'cancelled'
    finally:
        backend.release()


def test_disabled_other_group_member_does_not_revoke_active_recipient(root):
    backend = service(root)
    try:
        group = backend.store.groups('test:alice', 'Group', ['swe-id', 'designer-id'], 'swe-id', 'g')
        backend.store.send('test:alice', 'first', 'hello', ['swe-id'], cid=group['id'])
        config = json.loads((root / 'config.json').read_text())
        config['profiles'][1]['enabled'] = False
        (root / 'config.json').write_text(json.dumps(config))
        tick(backend)
        assert len(backend.runtime.calls) == 1
        backend.runtime.finish()
        tick(backend)
        assert backend.store.history('test:alice', group['id'])['runs'][0]['status'] == 'completed'
    finally:
        backend.release()
