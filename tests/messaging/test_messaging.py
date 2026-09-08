import concurrent.futures
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot_coms_messaging.api import create_router
from bot_coms_messaging.store import Problem, Store
from bot_coms_messaging.worker import Worker


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['bot-coms', 'bot-coms-messaging']}}))
    tmp_path = tmp_path / 'plugin-data' / 'bot-coms-messaging'
    tmp_path.mkdir(parents=True)
    config = dict(server_id='test-server', hermes_executable='/usr/bin/true', profiles=[
        dict(id='swe-id', peer='swe', name='swe', display_name='SWE', enabled=True, principals=['test:alice']),
        dict(id='designer-id', peer='designer', name='designer', display_name='Designer', enabled=True, principals=['test:alice'])])
    (tmp_path / 'config.json').write_text(json.dumps(config))
    return tmp_path


@pytest.fixture
def api(root):
    app = FastAPI()
    @app.middleware('http')
    async def identity(request, call_next):
        # Test auth middleware only. The production router trusts Hermes' verified session.
        if request.headers.get('test-user'):
            request.state.session = SimpleNamespace(provider='test', user_id=request.headers['test-user'], org_id=None)
        return await call_next(request)
    app.include_router(create_router(lambda: root))
    class ScopedClient(TestClient):
        def request(self, method, url, **kwargs):
            params = kwargs.pop('params', None)
            if params is None:
                params = {'expected_server': 'test-server', 'expected_principal': 'test:alice'}
            return super().request(method, url, params=params, **kwargs)
    return ScopedClient(app)


def headers():
    return {'test-user': 'alice'}


def send(api, mid='first', body='hello'):
    return api.post('/v1/dms/swe-id/messages', headers=headers(), json=dict(client_message_id=mid, body=body, recipients=[]))


def test_open_is_read_only_and_first_send_is_atomic(api, root):
    assert api.get('/v1/dms/swe-id', headers=headers()).status_code == 404
    assert api.get('/v1/conversations', headers=headers()).json()['conversations'] == []
    first = send(api).json()
    assert send(api).json() == first
    assert send(api, body='changed').status_code == 409
    with Store(root).db() as db:
        assert db.execute('SELECT count(*) FROM dispatches').fetchone()[0] == 1
    assert api.get('/v1/dms/swe-id/messages/by-client-id/first', headers=headers()).json() == first


def test_concurrent_devices_share_dm(root):
    store = Store(root)
    def submit(i):
        return store.send('test:alice', str(i), f'message {i}', [], dm=('swe-id', 'SWE'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(20)))
    assert len({r['conversation']['id'] for r in results}) == 1
    assert sorted(r['message']['sequence'] for r in results) == list(range(1,21))


def test_auth_and_forged_recipients(api):
    assert api.get('/v1/capabilities').status_code == 401
    assert api.get('/v1/capabilities', headers={'test-user': 'bob'}).status_code == 403
    cid = send(api).json()['conversation']['id']
    assert api.get('/v1/conversations/' + cid, headers={'test-user': 'bob'}).status_code == 403
    result = api.post('/v1/dms/swe-id/messages', headers=headers(), json=dict(client_message_id='forged', body='hello', recipients=['designer-id']))
    assert result.status_code == 403


def test_capability_requires_worker(api, root):
    assert api.get('/v1/capabilities', headers=headers()).json()['state'] == 'needs_configuration'
    worker = Worker(root, execute=lambda *_: 'answer')
    worker.heartbeat()
    assert api.get('/v1/capabilities', headers=headers()).json()['state'] == 'ready'


def test_real_spool_delivery_and_attributed_reply_survive_reopen(api, root):
    calls = []
    def execute(profile, dispatch, context, lease):
        calls.append(profile['id'])
        assert context[-1]['body'] == 'hello'
        return 'A useful answer'
    worker = Worker(root, execute=execute)
    cid = send(api).json()['conversation']['id']
    profile = worker.config['profiles'][0]
    assert worker.step(profile)
    assert not worker.step(profile)
    assert calls == ['swe-id']
    history = Store(root).history('test:alice', cid)
    assert history['messages'][-1]['author'] == 'swe-id'
    assert history['messages'][-1]['body'] == 'A useful answer'
    assert history['conversation']['unread'] == 1
    assert history['runs'][0]['status'] == 'completed'


def test_ambiguous_launch_is_not_replayed(api, root):
    called = []
    worker = Worker(root, execute=lambda *_: called.append(True))
    cid = send(api).json()['conversation']['id']
    with worker.store.db() as db:
        db.execute("UPDATE dispatches SET state='running'")
    worker.step(worker.config['profiles'][0])
    assert called == []
    assert worker.store.history('test:alice', cid)['runs'][0]['status'] == 'needs_attention'


def test_publish_after_cancel_and_removed_member_is_rejected(root):
    s = Store(root)
    g = s.groups('test:alice', 'Project', ['swe-id','designer-id'], 'swe-id', 'group1')
    s.send('test:alice','m1','hello',[],cid=g['id'],revision=1)
    with s.db() as db:
        d = db.execute('SELECT id FROM dispatches').fetchone()[0]
        db.execute("UPDATE dispatches SET state='running'")
    s.run_action('test:alice', d, 'cancel')
    assert not s.finish(d, body='late result')
    assert len(s.history('test:alice',g['id'])['messages']) == 1


def test_group_default_and_mentions_are_explicit(root):
    s = Store(root)
    g = s.groups('test:alice', 'Project', ['swe-id','designer-id'], 'swe-id', 'group1')
    s.send('test:alice','m1','quoted @designer does not route',[],cid=g['id'],revision=1)
    s.send('test:alice','m2','both please',['designer-id','swe-id','swe-id'],cid=g['id'],revision=1)
    with s.db() as db:
        assert [r[0] for r in db.execute('SELECT profile FROM dispatches ORDER BY created')] == ['swe-id','designer-id','swe-id']
    with pytest.raises(Problem) as error:
        s.send('test:alice','m3','old',[],cid=g['id'],revision=0)
    assert error.value.status == 409


def test_monotonic_read_and_archive_identity(api, root):
    worker = Worker(root, execute=lambda *_: 'reply')
    cid = send(api).json()['conversation']['id']
    worker.step(worker.config['profiles'][0])
    s = Store(root)
    assert s.read('test:alice',cid,1000)['sequence'] == 2
    assert s.read('test:alice',cid,0)['sequence'] == 2
    s.user_state('test:alice',cid,dict(archived=True))
    assert send(api, mid='later').status_code == 409
    assert s.dm_id('test:alice','swe-id') == cid
    s.user_state('test:alice',cid,dict(archived=False))
    assert send(api,mid='later').status_code == 200


def test_revocation_blocks_shared_history_and_dispatch(api, root):
    cid = send(api).json()['conversation']['id']
    config = json.loads((root/'config.json').read_text())
    config['profiles'][0]['principals'] = []
    (root/'config.json').write_text(json.dumps(config))
    assert api.get('/v1/conversations/'+cid,headers=headers()).status_code == 403
    worker = Worker(root,execute=lambda *_: pytest.fail('revoked work ran'))
    worker.step(worker.config['profiles'][0])
    with worker.store.db() as db:
        assert db.execute('SELECT state FROM dispatches').fetchone()[0] == 'cancelled'


def test_history_pagination_and_event_replay(root):
    s=Store(root)
    for i in range(105):
        result=s.send('test:alice',str(i),str(i),[],dm=('swe-id','SWE'))
    cid=result['conversation']['id']
    recent=s.history('test:alice',cid)
    older=s.history('test:alice',cid,recent['before'])
    assert len(recent['messages']) == 100 and len(older['messages']) == 5
    assert not ({m['id'] for m in recent['messages']} & {m['id'] for m in older['messages']})
    first=s.events('test:alice',0)
    assert s.events('test:alice',first['cursor'])['events'] == []


def test_quiet_subprocess_contract_uses_fresh_profile_and_private_diagnostics(api, root):
    import sys
    executable = root/'fake-hermes'
    executable.write_text(f'#!{sys.executable}\n' + '''import sys
from pathlib import Path
args = sys.argv[1:]
assert args[:3] == ['-p','swe','chat']
assert '--continue' not in args and '--resume' not in args
query = Path(args[args.index('--query-file')+1]).read_text()
assert 'hello' in query
print('Public final reply')
print('private diagnostics; session_id: runtime-123', file=sys.stderr)
''')
    executable.chmod(0o700)
    config = json.loads((root/'config.json').read_text())
    config['hermes_executable'] = str(executable)
    (root/'config.json').write_text(json.dumps(config))
    cid = send(api).json()['conversation']['id']
    worker = Worker(root)
    worker.step(worker.config['profiles'][0])
    h = worker.store.history('test:alice',cid)
    assert h['messages'][-1]['body'] == 'Public final reply'
    assert 'private' not in json.dumps(h)


def test_revoked_during_execution_cannot_publish(api,root):
    def execute(*_):
        config=json.loads((root/'config.json').read_text())
        config['profiles'][0]['principals']=[]
        (root/'config.json').write_text(json.dumps(config))
        return 'must not publish'
    worker=Worker(root,execute=execute)
    cid=send(api).json()['conversation']['id']
    worker.step(worker.config['profiles'][0])
    assert len(Store(root).history('test:alice',cid)['messages']) == 1


def test_disabling_plugin_stops_api_and_worker_before_restart(api, root):
    send(api)
    worker = Worker(root, execute=lambda *_: pytest.fail('disabled worker ran'))
    (root.parent.parent/'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['bot-coms']}}))
    assert api.get('/v1/capabilities',headers=headers()).json()['state'] == 'needs_configuration'
    assert send(api,mid='disabled').status_code == 503
    with pytest.raises(Problem):
        worker.step(worker.config['profiles'][0])


def test_crash_after_spool_put_before_outbox_receipt_does_not_double_run(api, root):
    calls=[]
    worker=Worker(root,execute=lambda *_: calls.append(True) or 'once')
    cid=send(api).json()['conversation']['id']
    original=worker.sender.send
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('injected death after publication')
    worker.sender.send=crash
    with pytest.raises(RuntimeError): worker.step(worker.config['profiles'][0])
    reopened=Worker(root,execute=lambda *_: calls.append(True) or 'once')
    reopened.step(reopened.config['profiles'][0])
    reopened.step(reopened.config['profiles'][0])
    assert calls == [True]
    assert len(Store(root).history('test:alice',cid)['messages']) == 2


def test_crash_after_reply_commit_before_ack_does_not_republish(api,root,monkeypatch):
    from bot_coms import Client
    calls=[]
    worker=Worker(root,execute=lambda *_: calls.append(True) or 'once')
    cid=send(api).json()['conversation']['id']
    original=Client.ack
    def crash(*args,**kwargs): raise RuntimeError('injected ack failure')
    monkeypatch.setattr(Client,'ack',crash)
    with pytest.raises(RuntimeError): worker.step(worker.config['profiles'][0])
    monkeypatch.setattr(Client,'ack',original)
    Worker(root,execute=lambda *_: pytest.fail('repeated launch')).step(worker.config['profiles'][0])
    assert len(Store(root).history('test:alice',cid)['messages']) == 2


def test_disabling_one_profile_is_not_account_revocation(api,root):
    cid=send(api).json()['conversation']['id']
    config=json.loads((root/'config.json').read_text())
    config['profiles'][0]['enabled']=False
    (root/'config.json').write_text(json.dumps(config))
    assert send(api,mid='later').status_code == 409
    assert api.get('/v1/conversations/'+cid,headers=headers()).status_code == 200
    assert api.post('/v1/dms/designer-id/messages',headers=headers(),json=dict(client_message_id='designer',body='hello',recipients=[])).status_code == 200


def test_large_unicode_history_stays_under_native_response_budget(root):
    store=Store(root)
    for i in range(20):
        receipt=store.send('test:alice',str(i),'😀'*32000,[],dm=('swe-id','SWE'))
    page=store.history('test:alice',receipt['conversation']['id'])
    assert len(json.dumps(page,ensure_ascii=False).encode()) < 2_000_000
    assert page['before'] is not None
    assert len(page['messages']) < 20


def test_stale_account_or_replaced_server_cannot_accept_draft(api,root):
    response=api.post('/v1/dms/swe-id/messages',headers=headers(),params={'expected_server':'old-server','expected_principal':'test:alice'},json=dict(client_message_id='stale',body='private draft',recipients=[]))
    assert response.status_code == 403
    with Store(root).db() as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
