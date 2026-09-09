import concurrent.futures
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot_coms_messaging.api import create_router
from bot_coms_messaging.store import Problem, Store


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








def test_publish_after_cancel_and_removed_member_is_rejected(root):
    s = Store(root)
    g = s.groups('test:alice', 'Project', ['swe-id','designer-id'], 'swe-id', 'group1')
    s.send('test:alice','m1','hello',['swe-id'],cid=g['id'],revision=1)
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
    with s.db() as db:
        assert db.execute('SELECT count(*) FROM dispatches').fetchone()[0] == 0
    s.send('test:alice','m2','both please',['designer-id','swe-id','swe-id'],cid=g['id'],revision=1)
    with s.db() as db:
        assert [r[0] for r in db.execute('SELECT profile FROM dispatches ORDER BY created')] == ['designer-id','swe-id']
    with pytest.raises(Problem) as error:
        s.send('test:alice','m3','old',[],cid=g['id'],revision=0)
    assert error.value.status == 409


def test_monotonic_read_and_archive_identity(api, root):
    cid = send(api).json()['conversation']['id']
    _run_dispatch(Store(root), 'swe-id', 'reply')
    s = Store(root)
    assert s.read('test:alice',cid,1000)['sequence'] == 2
    assert s.read('test:alice',cid,0)['sequence'] == 2
    s.user_state('test:alice',cid,dict(archived=True))
    assert send(api, mid='later').status_code == 409
    assert s.dm_id('test:alice','swe-id') == cid
    s.user_state('test:alice',cid,dict(archived=False))
    assert send(api,mid='later').status_code == 200


def test_delete_group_removes_state_and_rejects_dm(api, root):
    import pytest
    from bot_coms_messaging.store import Problem
    s = Store(root)
    group = s.groups('test:alice', 'Room', ['swe-id', 'designer-id'], 'swe-id', 'delete-me')
    s.send('test:alice', 'g1', 'hello', [], cid=group['id'], revision=1)
    with s.db() as db:
        db.execute(
            'INSERT INTO session_bindings(binding_key,server_id,owner,conversation,profile,profile_name) '
            "VALUES('bk','test-server','test:alice',?,'swe-id','swe')",
            (group['id'],),
        )
    result = s.delete_group('test:alice', group['id'])
    assert result['ok'] is True
    assert result['binding_keys'] == ['bk']
    with pytest.raises(Problem) as missing:
        s.history('test:alice', group['id'])
    assert missing.value.status == 404
    with s.db() as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM dispatches').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM session_bindings').fetchone()[0] == 0
        kinds = [r[0] for r in db.execute('SELECT kind FROM events ORDER BY sequence')]
        assert kinds[-1] == 'conversation.deleted'
    again = s.groups('test:alice', 'Room', ['swe-id', 'designer-id'], 'swe-id', 'delete-me-2')
    assert again['id'] != group['id']
    dm = send(api).json()['conversation']['id']
    with pytest.raises(Problem) as dm_err:
        s.delete_group('test:alice', dm)
    assert dm_err.value.status == 422


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


def test_peer_from_profile_name_avoids_crew_collisions():
    from bot_coms_messaging.config import peer_from_profile_name, stable_profile_id
    assert peer_from_profile_name('software-engineer') == 'software-engineer'
    assert peer_from_profile_name('mora-software-engineer') == 'mora-software-engineer'
    assert peer_from_profile_name('software-engineer') != peer_from_profile_name('mora-software-engineer')
    a = stable_profile_id('server', 'software-engineer')
    b = stable_profile_id('server', 'mora-software-engineer')
    assert a != b and len(a) == 32


def test_auto_enroll_discovers_profiles_and_honours_overrides(tmp_path):
    from bot_coms_messaging.config import load_config, Problem
    hermes = tmp_path
    (hermes / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['bot-coms', 'bot-coms-messaging']}}))
    (hermes / 'profiles' / 'software-engineer').mkdir(parents=True)
    (hermes / 'profiles' / 'software-engineer' / 'profile.yaml').write_text('display_name: SWE\n')
    (hermes / 'profiles' / 'mora-software-engineer').mkdir(parents=True)
    (hermes / 'profiles' / 'opt-out').mkdir(parents=True)
    messaging = hermes / 'plugin-data' / 'bot-coms-messaging'
    messaging.mkdir(parents=True)
    (messaging / 'config.json').write_text(json.dumps({
        'server_id': 'srv',
        'hermes_executable': '/usr/bin/true',
        'default_principals': ['test:alice'],
        'auto_enroll_profiles': True,
        'profiles': [
            {'name': 'opt-out', 'enabled': False},
        ],
    }))
    config = load_config(messaging)
    by_name = {p['name']: p for p in config['profiles']}
    assert set(by_name) >= {'default', 'software-engineer', 'mora-software-engineer', 'opt-out'}
    assert by_name['software-engineer']['display_name'] == 'SWE'
    assert by_name['software-engineer']['peer'] == 'software-engineer'
    assert by_name['mora-software-engineer']['peer'] == 'mora-software-engineer'
    assert by_name['opt-out']['enabled'] is False
    assert by_name['default']['principals'] == ['test:alice']

    bad = json.loads((messaging / 'config.json').read_text())
    bad.pop('default_principals')
    (messaging / 'config.json').write_text(json.dumps(bad))
    with pytest.raises(Problem) as err:
        load_config(messaging)
    assert 'default_principals' in err.value.detail




PROFILES = [
    dict(id='swe-id', peer='swe', name='swe', display_name='SWE', enabled=True, principals=['test:alice']),
    dict(id='designer-id', peer='designer', name='designer', display_name='Designer', enabled=True, principals=['test:alice']),
]


def _run_dispatch(store, profile_id, body, profiles=None, **kwargs):
    with store.db() as db:
        d = db.execute(
            "SELECT id FROM dispatches WHERE profile=? AND state='queued' ORDER BY created LIMIT 1",
            (profile_id,),
        ).fetchone()
        assert d is not None
        db.execute("UPDATE dispatches SET state='running' WHERE id=?", (d['id'],))
        dispatch_id = d['id']
    assert store.finish(dispatch_id, body=body, profiles=profiles or PROFILES, **kwargs)


def test_bot_output_at_enqueues_member_handoff(root):
    s = Store(root)
    g = s.groups('test:alice', 'Project', ['swe-id', 'designer-id'], 'swe-id', 'handoff')
    s.send('test:alice', 'm1', 'please coordinate', ['swe-id'], cid=g['id'], revision=1)
    _run_dispatch(s, 'swe-id', 'I need @designer-id on the layout')
    with s.db() as db:
        rows = [dict(r) for r in db.execute(
            'SELECT id, profile, hop, parent_dispatch FROM dispatches ORDER BY created')]
    assert [r['profile'] for r in rows] == ['swe-id', 'designer-id']
    assert rows[0]['hop'] == 0 and rows[0]['parent_dispatch'] is None
    assert rows[1]['hop'] == 1 and rows[1]['parent_dispatch'] == rows[0]['id']


def test_bot_mention_name_alias_does_not_route(root):
    s = Store(root)
    g = s.groups('test:alice', 'Project', ['swe-id', 'designer-id'], 'swe-id', 'alias')
    s.send('test:alice', 'm1', 'please coordinate', ['swe-id'], cid=g['id'], revision=1)
    _run_dispatch(s, 'swe-id', 'I need @Designer and @designer on the layout')
    with s.db() as db:
        assert [r[0] for r in db.execute('SELECT profile FROM dispatches')] == ['swe-id']


def test_bot_mention_in_code_fence_does_not_route(root):
    s = Store(root)
    g = s.groups('test:alice', 'Project', ['swe-id', 'designer-id'], 'swe-id', 'fence')
    s.send('test:alice', 'm1', 'look at this', ['swe-id'], cid=g['id'], revision=1)
    _run_dispatch(s, 'swe-id', 'Example:\n```\n@designer-id in a fence\n```\nand ` @swe-id ` inline')
    with s.db() as db:
        assert [r[0] for r in db.execute('SELECT profile FROM dispatches')] == ['swe-id']


def test_bot_mention_cycle_and_hop_budget(root):
    s = Store(root)
    g = s.groups('test:alice', 'Project', ['swe-id', 'designer-id'], 'swe-id', 'cycle')
    s.send('test:alice', 'm1', 'start', ['swe-id'], cid=g['id'], revision=1)
    _run_dispatch(s, 'swe-id', 'Handing to @designer-id')
    _run_dispatch(s, 'designer-id', 'Back to you @swe-id')
    with s.db() as db:
        profiles = [r[0] for r in db.execute('SELECT profile FROM dispatches ORDER BY created')]
    # A→B is allowed; B→A is dropped because swe is already on the chain.
    assert profiles == ['swe-id', 'designer-id']


def test_bot_mention_hop_cap_drops_third_hop(root):
    s = Store(root)
    profiles = PROFILES + [
        dict(id='pm-id', peer='pm', name='pm', display_name='PM', enabled=True, principals=['test:alice']),
    ]
    # Expand group membership for a three-hop attempt.
    with open(root / 'config.json', 'w') as fh:
        json.dump(dict(server_id='test-server', hermes_executable='/usr/bin/true', profiles=profiles), fh)
    g = s.groups('test:alice', 'Project', ['swe-id', 'designer-id', 'pm-id'], 'swe-id', 'hops')
    s.send('test:alice', 'm1', 'start', ['swe-id'], cid=g['id'], revision=1)
    _run_dispatch(s, 'swe-id', 'Ask @designer-id', profiles=profiles)
    _run_dispatch(s, 'designer-id', 'Ask @pm-id', profiles=profiles)
    # hop=2 reply trying to create hop=3 must be dropped (max_mention_hops=2).
    _run_dispatch(s, 'pm-id', 'Ask @swe-id again', profiles=profiles)
    with s.db() as db:
        hops = [dict(r) for r in db.execute('SELECT profile, hop FROM dispatches ORDER BY created')]
    assert [(r['profile'], r['hop']) for r in hops] == [
        ('swe-id', 0), ('designer-id', 1), ('pm-id', 2),
    ]


def test_bot_mention_wake_budget_per_origin(root):
    s = Store(root)
    extras = [
        dict(id=f'bot{i}-id', peer=f'bot{i}', name=f'bot{i}', display_name=f'Bot{i}',
             enabled=True, principals=['test:alice'])
        for i in range(5)
    ]
    members = ['swe-id'] + [p['id'] for p in extras]
    profiles = PROFILES + extras
    with open(root / 'config.json', 'w') as fh:
        json.dump(dict(server_id='test-server', hermes_executable='/usr/bin/true', profiles=profiles), fh)
    g = s.groups('test:alice', 'Room', members, 'swe-id', 'budget')
    s.send('test:alice', 'm1', 'fan out', ['swe-id'], cid=g['id'], revision=1)
    body = ' '.join(f'@{p["id"]}' for p in extras)
    _run_dispatch(s, 'swe-id', body, profiles=profiles, max_wakes_per_origin=4)
    with s.db() as db:
        # Origin user dispatch + at most 3 follow-ups (budget 4 total).
        assert db.execute('SELECT count(*) FROM dispatches').fetchone()[0] == 4


def test_mention_parser_unit():
    from bot_coms_messaging.mentions import resolve_mentions, strip_code
    assert '@swe' not in strip_code('see `@swe` and ```\n@designer\n```')
    members = [
        {'id': 'swe-id', 'name': 'swe', 'display_name': 'SWE'},
        {'id': 'designer-id', 'name': 'designer', 'display_name': 'Designer'},
    ]
    assert resolve_mentions('Ping @Designer and @swe', members) == []
    assert resolve_mentions('Ping @designer-id and @SWE-ID', members) == ['designer-id', 'swe-id']
    assert resolve_mentions('Ask @{designer-id} next', members) == ['designer-id']
    assert resolve_mentions('Ask @{designer-id} and @swe-id', members) == ['designer-id', 'swe-id']
    assert resolve_mentions('no one', members) == []


def test_fresh_store_can_insert_turn_decisions(root):
    s = Store(root)
    g = s.groups('test:alice', 'Room', ['swe-id', 'designer-id'], 'swe-id', 'turns')
    sent = s.send('test:alice', 'm1', 'hello', [], cid=g['id'], revision=1)
    mid = sent['message']['id']
    s.save_turn_decision(
        mid,
        input_seq=1,
        policy_version='1',
        model='stub',
        action='select',
        speaker='swe-id',
        reason='ack',
        shadow=False,
    )
    with s.db() as db:
        row = db.execute('SELECT action,speaker,reason FROM turn_decisions WHERE message=?', (mid,)).fetchone()
        assert row['action'] == 'select'
        assert row['speaker'] == 'swe-id'
        assert row['reason'] == 'ack'
