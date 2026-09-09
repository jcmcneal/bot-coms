import asyncio
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import ContextVar
from types import ModuleType

import pytest

from bot_coms import Client, init_spool
from bot_coms.team_runtime import TeamRuntime, enqueue_wake


class Native:
    def __init__(self):
        self.calls = []
        self.receipts = {}
        self.sessions = {}
        self.fail_after_admit = False

    def status(self, *, principal_id, operation_key):
        return self.receipts.get((principal_id, operation_key))

    def submit(self, **args):
        self.calls.append(args)
        key = args['principal_id'], args['operation_key']
        session_key = args['principal_id'], args['profile'], args['conversation_key']
        session = self.sessions.setdefault(session_key, 'session-' + str(len(self.sessions) + 1))
        self.receipts[key] = dict(status='running', session_id=session, error=None)
        if self.fail_after_admit:
            self.fail_after_admit = False
            raise ConnectionError('lost admission reply')
        return self.receipts[key]

    def cancel(self, *, principal_id, operation_key):
        receipt = self.receipts.get((principal_id, operation_key))
        if receipt:
            receipt['status'] = 'cancelled'
        return receipt

    def finish(self, index=0, status='completed'):
        call = self.calls[index]
        self.receipts[call['principal_id'], call['operation_key']]['status'] = status


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    team = tmp_path / 'team'
    init_spool(team / 'spool', ['sender', 'worker', 'owner'])
    for peer in ('sender', 'worker', 'owner'):
        home = tmp_path / 'profiles' / peer
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['bot-coms', 'bot-coms-board']}}))
    monkeypatch.setenv('BOT_COMS_TEAM_ROOT', str(team))
    monkeypatch.setenv('BOT_COMS_SPOOL_ROOT', str(team / 'spool'))
    native = Native()
    service = TeamRuntime(team_root=team, executor=native)
    yield service, native
    service.close()


def send(runtime, *, to='worker', scope='S1', kind='assign'):
    service, _ = runtime
    with closing(Client(service.spool_root, 'sender')) as sender:
        return sender.send(to, 'event', {'schema_version': '1.0', 'intent': kind, 'slice': scope} if scope else {'question': 'What do you think?'})


def tick(runtime):
    return asyncio.run(runtime[0].tick())


def rows(service):
    with sqlite3.connect(service.team_root / 'team-runtime.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM turns ORDER BY created_at,id')]


def test_durable_enqueue_never_spawns_and_cross_process_scan_recovers(runtime, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('subprocess wake'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('subprocess wake'))
    env = send(runtime)
    service, native = runtime
    # The doorbell is disabled, as if a producer died just after spool commit.
    assert not (service.team_root / 'team-runtime.sqlite3').exists()
    tick(runtime)
    assert native.calls[0]['session_env']['BOT_COMS_MESSAGE_ID'] == env.id
    assert rows(service)[0]['status'] == 'running'


def test_same_assignment_reuses_exact_session_but_serializes_turns(runtime):
    service, native = runtime
    send(runtime)
    send(runtime, kind='report')
    tick(runtime)
    assert len(native.calls) == 1
    native.finish()
    tick(runtime)
    assert len(native.calls) == 2
    assert native.calls[0]['conversation_key'] == native.calls[1]['conversation_key'] == 'assignment:S1'
    assert len(native.sessions) == 1


def test_assignments_and_return_peers_are_isolated(runtime):
    _, native = runtime
    send(runtime, scope='S1')
    send(runtime, scope='S2')
    send(runtime, scope='S1', to='owner', kind='report')
    tick(runtime)
    assert len(native.calls) == len(native.sessions) == 3
    assert {call['session_env']['BOT_COMS_PEER_ID'] for call in native.calls} == {'worker', 'owner'}


def test_lost_admission_reply_restart_attaches_instead_of_replaying(runtime):
    service, native = runtime
    native.fail_after_admit = True
    send(runtime)
    tick(runtime)
    assert rows(service)[0]['status'] == 'admitting'
    service.close()
    restored = TeamRuntime(team_root=service.team_root, executor=native)
    try:
        asyncio.run(restored.tick())
        assert len(native.calls) == 1
        assert rows(restored)[0]['status'] == 'running'
    finally:
        restored.close()


@pytest.mark.parametrize('status', ['failed', 'indeterminate'])
def test_failed_or_uncertain_native_turn_needs_attention_not_automatic_replay(runtime, status):
    service, native = runtime
    send(runtime)
    tick(runtime)
    native.finish(status=status)
    result = tick(runtime)
    tick(runtime)
    assert len(native.calls) == 1
    assert result['attention'][0]['status'] == status


def test_arbitrary_a2a_preserves_payload_and_correlation_without_team_inbox(runtime):
    _, native = runtime
    env = send(runtime, scope=None)
    tick(runtime)
    call = native.calls[0]
    assert call['conversation_key'] == 'a2a:' + env.correlation_id
    assert 'bot_coms_claim' in call['text'] and 'bot_coms_ack' in call['text']
    assert 'team_inbox' not in call['text']
    assert 'What do you think?' in call['text']


def test_second_backend_and_live_legacy_child_are_fenced(runtime):
    service, native = runtime
    service.start()
    second = TeamRuntime(team_root=service.team_root, executor=native)
    with pytest.raises(RuntimeError, match='already owned'):
        second.start()
    service.close()
    import fcntl
    directory = service.team_root / 'wake-state/worker'
    directory.mkdir(parents=True)
    with (directory / 'session.lock').open('a+b') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='legacy wake is still running'):
            service.start()


def test_legacy_import_preserves_unknown_text_and_exact_known_assignment(runtime):
    service, native = runtime
    env = send(runtime)
    directory = service.team_root / 'wake-state/worker'
    directory.mkdir(parents=True)
    (directory / f'{env.id}.txt').write_text('old wake')
    (directory / 'unknown.txt').write_text('do not lose this text')
    tick(runtime)
    assert len(native.calls) == 1
    assert len(list(directory.glob('*.txt.migrated'))) == 2
    unknown = next(row for row in rows(service) if row['id'].startswith('legacy:'))
    assert unknown['status'] == 'indeterminate' and unknown['text'] == 'do not lose this text'


def test_profile_revocation_cancels_active_native_operation(runtime):
    service, native = runtime
    send(runtime)
    tick(runtime)
    config = service.team_root.parent / 'profiles/worker/config.yaml'
    config.write_text(json.dumps({'plugins': {'enabled': []}}))
    tick(runtime)
    assert rows(service)[0]['status'] == 'cancelled'
    send(runtime, scope='S2')
    tick(runtime)
    assert len(native.calls) == 1


def test_session_context_peer_identity_does_not_leak_between_threads(runtime, monkeypatch):
    service, _ = runtime
    context = ContextVar('test_session', default={})
    module = ModuleType('gateway.session_context')
    module.get_session_env = lambda key, default=None: context.get().get(key, default)
    monkeypatch.setitem(sys.modules, 'gateway.session_context', module)
    monkeypatch.setenv('BOT_COMS_PEER_ID', 'wrong-peer')
    from hermes_bot_coms.tools import _client

    def check(peer):
        token = context.set({'BOT_COMS_PEER_ID': peer, 'BOT_COMS_SPOOL_ROOT': str(service.spool_root)})
        try:
            with closing(_client()) as client:
                return client.peer_id
        finally:
            context.reset(token)
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(check, ['worker', 'owner'])) == ['worker', 'owner']


def test_already_acked_mail_is_not_admitted_from_durable_doorbell(runtime, monkeypatch):
    service, native = runtime
    monkeypatch.setenv('BOT_COMS_DOORBELL', '1')
    env = send(runtime)
    with closing(Client(service.spool_root, 'worker')) as worker:
        worker.ack(worker.claim(env.id), result={})
    tick(runtime)
    assert native.calls == []
    assert rows(service)[0]['status'] == 'cancelled'


def test_generic_payload_named_assign_does_not_require_board_plugin(runtime):
    service, native = runtime
    (service.team_root.parent / 'profiles/worker/config.yaml').write_text(json.dumps({'plugins': {'enabled': ['bot-coms']}}))
    with closing(Client(service.spool_root, 'sender')) as sender:
        sender.send('worker', 'request', {'intent': 'assign', 'description': 'arbitrary protocol payload'})
    tick(runtime)
    assert len(native.calls) == 1 and 'bot_coms_claim' in native.calls[0]['text']


def test_bound_generic_claim_cannot_consume_another_assignment(runtime, monkeypatch):
    service, _ = runtime
    first = send(runtime, scope='S1')
    second = send(runtime, scope='S2')
    monkeypatch.setenv('BOT_COMS_PEER_ID', 'worker')
    monkeypatch.setenv('BOT_COMS_MESSAGE_ID', first.id)
    from hermes_bot_coms.tools import bot_coms_claim
    with pytest.raises(ValueError, match='different message'):
        bot_coms_claim({'id': second.id})
    result = json.loads(bot_coms_claim({}))
    assert result['envelope']['id'] == first.id
    assert (service.spool_root / 'worker/inbox' / f'{second.id}.json').exists()


def test_board_inbox_bound_message_does_not_drain_other_assignment(runtime):
    service, _ = runtime
    first = send(runtime, scope='S1')
    second = send(runtime, scope='S2')
    from bot_coms_board.coordinator import TeamCoordinator
    with TeamCoordinator(team_root=service.team_root, spool_root=service.spool_root) as coordinator:
        result = coordinator.process_inbox('worker', message_id=first.id)
    assert [decision.message_id for decision in result.decisions] == [first.id]
    assert (service.spool_root / 'worker/inbox' / f'{second.id}.json').exists()


def test_completed_turn_with_unconsumed_mail_surfaces_attention(runtime):
    service, native = runtime
    send(runtime)
    tick(runtime)
    native.finish()
    tick(runtime)
    result = tick(runtime)
    assert len(native.calls) == 1
    assert result['attention'][0]['status'] == 'indeterminate'
    assert 'inspect before retrying' in result['attention'][0]['error']


def test_explicit_retry_keeps_session_but_requires_uncertain_effect_review(runtime):
    service, native = runtime
    env = send(runtime)
    tick(runtime)
    native.finish(status='indeterminate')
    tick(runtime)
    with pytest.raises(ValueError, match='inspect uncertain effects'):
        service.retry(env.id)
    service.retry(env.id, allow_uncertain=True)
    tick(runtime)
    assert len(native.calls) == 2 and len(native.sessions) == 1
    assert native.calls[0]['operation_key'] != native.calls[1]['operation_key']
    assert native.calls[0]['conversation_key'] == native.calls[1]['conversation_key']


def test_arbitrary_request_claim_ack_and_return_receipt_roundtrip(runtime, monkeypatch):
    service, native = runtime
    context = ContextVar('roundtrip_session', default={})
    module = ModuleType('gateway.session_context')
    module.get_session_env = lambda key, default=None: context.get().get(key, default)
    monkeypatch.setitem(sys.modules, 'gateway.session_context', module)
    from hermes_bot_coms.tools import bot_coms_claim, bot_coms_ack
    original_submit = native.submit

    def execute(**args):
        receipt = original_submit(**args)
        token = context.set(args['session_env'])
        try:
            claimed = json.loads(bot_coms_claim({}))
            bot_coms_ack({'id': claimed['envelope']['id'], 'lease_token': claimed['lease_token'],
                          'result': {'answer': 'Yes, that interface is compatible.'}})
        finally:
            context.reset(token)
        receipt['status'] = 'completed'
        return receipt
    native.submit = execute
    with closing(Client(service.spool_root, 'sender')) as sender:
        envelope = sender.send('worker', 'request', {'question': 'Is the interface compatible?'}, reply_to='sender')
        tick(runtime)
        response = sender.poll_result(envelope.correlation_id)
        assert response.payload == {'answer': 'Yes, that interface is compatible.'}
    tick(runtime)
    assert len(native.calls) == 1  # receipt is not a new agent wake
    assert rows(service)[0]['status'] == 'completed'


@pytest.mark.parametrize('invalid', [
    'plugins: [',
    '{"plugins":{"enabled":"bot-coms"}}',
    '{"plugins":{"enabled":["bot-coms","bot-coms-board"],"disabled":"other"}}',
])
def test_invalid_target_config_cancels_running_turn(runtime, invalid):
    service, native = runtime
    send(runtime)
    tick(runtime)
    (service.team_root.parent / 'profiles' / 'worker' / 'config.yaml').write_text(invalid)
    tick(runtime)
    assert next(iter(native.receipts.values()))['status'] == 'cancelled'
