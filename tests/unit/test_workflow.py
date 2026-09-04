from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from bot_coms import Client, init_spool, Worker
from bot_coms_board.coordinator import TeamCoordinator
from bot_coms_board.store import BusStore


@pytest.fixture
def loop(tmp_path, monkeypatch):
    team = tmp_path / 'team'; spool = team / 'spool'
    init_spool(spool, ['requester', 'lead', 'builder', 'auditor', 'owner', 'replacement'])
    monkeypatch.setenv('BOT_COMS_TEAM_ROOT', str(team))
    monkeypatch.setenv('BOT_COMS_SPOOL_ROOT', str(spool))
    monkeypatch.delenv('BOT_COMS_PEER_ID', raising=False)
    config = {
        'version':1,
        'peers':{p:{'profile':p, 'capabilities':['build','check']} for p in ['requester','lead','builder','auditor','owner','replacement']},
        'bindings':{'reviewer':'auditor','acceptor':'owner'},
        'defaults':{'implement':'delivery'},
        'policies':{'delivery':{'owner':'acceptor','worker_capability':'build',
                              'gates':[{'id':'quality','responsibility':'reviewer','capability':'check','independent':True}]}}
    }
    (team/'workflows.json').write_text(json.dumps(config))
    context = team/'context'; context.mkdir(); (context/'S1.md').write_text('Deliver feature. Tests and artifact references required.')
    c = TeamCoordinator(team_root=team, spool_root=spool)
    yield c, config
    c.store.close()


def assign(c, sid='S1', **kw):
    return c.assign(slice_id=sid, to_peer=kw.pop('to_peer','builder'), from_peer=kw.pop('from_peer','lead'),
                    title='Task', assignment_path=str(c.team_root/'context/S1.md'), activity=kw.pop('activity','implement'), **kw)


def submit(c, evidence='commit abc; pytest passed; artifact /review/result'):
    return c.report(slice_id='S1', from_peer='builder', verdict='SUBMITTED', evidence=evidence)


def approve(c, revision=1, decision='APPROVED'):
    return c.workflow('review', actor='auditor', slice_id='S1', gate='quality', revision=revision,
                      decision=decision, evidence='Independent checks passed against commit abc')


def accept(c, revision=1):
    return c.workflow('accept', actor='owner', slice_id='S1', revision=revision, evidence='Delivered feature, verified commit abc. NEXT: follow-up.')


def test_arbitrary_org_full_roundtrip(loop, monkeypatch):
    c,_=loop; assign(c, headers={'source':'spm:request'})
    assert c.store.get_slice('S1').contract['return_peer']=='lead'
    assert c.process_inbox('builder').decisions[0].disposition=='launch_agent'
    c.store.set_status('S1','RUNNING',active_job='job')
    c.report(slice_id='S1',from_peer='builder',verdict='EXECUTED',evidence='exit=0',job='job')
    with pytest.raises(ValueError, match='submitted revision'): accept(c, revision=0)
    submit(c)
    with pytest.raises(ValueError,match='reviews'): accept(c)
    approve(c)
    notifications=[]
    monkeypatch.setenv('BOT_COMS_DOORBELL','1')
    with patch('bot_coms.doorbell._wake_runner',lambda *args:None), patch('bot_coms.doorbell._adapter_runner',lambda source,env:notifications.append(env.payload)):
        accept(c)
        accept(c) # idempotent acceptance must not enqueue a second outward notification
    assert c.store.get_slice('S1').status=='DONE'
    assert len(notifications)==1
    assert notifications[0]['verdict']=='ACCEPTED'
    assert 'NEXT:' in notifications[0]['evidence']


def test_config_changes_only_new_assignments(loop):
    c,config=loop; assign(c)
    config['bindings']['reviewer']='replacement'
    (c.team_root/'workflows.json').write_text(json.dumps(config))
    assign(c, sid='S2')
    assert c.store.get_slice('S1').contract['gates'][0]['peer']=='auditor'
    assert c.store.get_slice('S2').contract['gates'][0]['peer']=='replacement'
    # Repeat assign uses its original contract and does not reset status.
    c.store.set_status('S1','RUNNING',active_job='job')
    assign(c)
    assert c.store.get_slice('S1').active_job=='job'
    assert c.store.get_slice('S1').contract['gates'][0]['peer']=='auditor'


def test_review_independence_and_revision(loop):
    c,_=loop;assign(c);submit(c)
    with pytest.raises(ValueError,match='reviewer'):
        c.workflow('review',actor='builder',slice_id='S1',gate='quality',revision=1,decision='APPROVED',evidence='self approved')
    approve(c,decision='REJECTED')
    with pytest.raises(ValueError):accept(c)
    submit(c,evidence='commit def; fixed rejection')
    with pytest.raises(ValueError,match='current'):approve(c,revision=1)
    with pytest.raises(ValueError,match='reviews'):accept(c,revision=2)
    approve(c,revision=2);accept(c,revision=2)
    reviews=[e for e in c.store.workflow_history('S1') if e['kind']=='review']
    assert len(reviews)==2 and reviews[0]['details']['decision']=='REJECTED'


def test_owner_and_raw_crud_cannot_bypass_gates(loop):
    c,_=loop;assign(c);submit(c)
    with pytest.raises(ValueError):c.store.set_status('S1','DONE')
    with pytest.raises(ValueError):c.store.set_verdict('S1',verdict='ACCEPTED')
    with pytest.raises(ValueError):c.workflow('accept',actor='builder',slice_id='S1',revision=1,evidence='yes')
    with pytest.raises(ValueError):c.store.register_slice(slice_id='S1',peer='replacement',to_profile='replacement',from_profile='lead',title='replace',assignment_path='x',content_sha256='x')


def test_reassign_invalidates_old_job_and_reviews(loop):
    c,_=loop;assign(c);submit(c);approve(c)
    c.workflow('reassign',actor='owner',slice_id='S1',to_peer='replacement',reason='Team changed',gate_peers={'quality':'lead'})
    row=c.store.get_slice('S1')
    assert row.peer=='replacement' and row.contract['return_peer']=='lead'
    assert row.contract['gates'][0]['peer']=='lead'
    assert row.contract['submission'] is None
    with pytest.raises(ValueError):submit(c)
    old=c.process_inbox('builder')
    assert old.decisions[0].disposition=='assign_inactive'
    with pytest.raises(ValueError):accept(c,revision=2)
    assert c.process_inbox('replacement').decisions[0].disposition=='launch_agent'


def test_parent_child_acceptance(loop):
    c,_=loop
    assign(c,sid='PARENT',from_peer='owner',to_peer='lead',activity='coordinate')
    assign(c,parent_slice='PARENT')
    c.report(slice_id='PARENT',from_peer='lead',verdict='SUBMITTED',evidence='Delivery summary')
    with pytest.raises(ValueError,match='child'):
        c.workflow('accept',actor='owner',slice_id='PARENT',revision=1,evidence='ready')
    submit(c);approve(c);accept(c)
    c.workflow('accept',actor='owner',slice_id='PARENT',revision=1,evidence='Accepted with child evidence')
    assert c.store.get_slice('PARENT').status=='DONE'


def test_paused_stale_exit_and_coordination(loop):
    c,_=loop;assign(c,activity='coordinate')
    d=c.process_inbox('builder').decisions[0]
    assert d.disposition=='coordinate'
    with pytest.raises(ValueError):c.store.set_status('S1','RUNNING')
    c.store.set_status('S1','RUNNING',active_job='job-new')
    with pytest.raises(ValueError,match='stale'):
        c.report(slice_id='S1',from_peer='builder',verdict='EXECUTED',evidence='exit=0',job='job-old')
    c.report(slice_id='S1',from_peer='builder',verdict='PAUSED',evidence='Need design answer',job='job-new')
    assert c.store.get_slice('S1').status=='PAUSED'
    assert c.store.get_slice('S1').active_job is None


def test_durable_outbox_recovers_without_duplicate_envelopes(loop,monkeypatch):
    c,_=loop;monkeypatch.setenv('BOT_COMS_DOORBELL','1')
    with patch('bot_coms.doorbell._wake_runner',side_effect=OSError('offline')):
        result=assign(c)
    pending=c.store.pending_deliveries()
    assert len(pending)==1 and pending[0]['last_error']=='offline'
    before=list((c.spool_root/'builder/inbox').glob('*.json'))
    import time
    future=time.time()+61
    with patch('time.time',return_value=future), patch('bot_coms.doorbell._wake_runner',lambda *a:None):c.flush_deliveries()
    after=list((c.spool_root/'builder/inbox').glob('*.json'))
    assert before==after and len(after)==1
    assert after[0].stem==result.outbound_id


def test_internal_report_does_not_notify_origin(loop,monkeypatch):
    c,_=loop;assign(c,headers={'source':'discord:channel'})
    monkeypatch.setenv('BOT_COMS_DOORBELL','1');wakes=[];sends=[]
    with patch('bot_coms.doorbell._wake_runner',lambda profile,peer,env:wakes.append(peer)), patch('bot_coms.doorbell._send_runner',lambda *a:sends.append(a)):
        submit(c)
    assert 'lead' in wakes and 'auditor' in wakes
    assert sends==[]


def test_ack_is_a_receipt_not_a_board_command(loop):
    c,_=loop;client=Client(c.spool_root,'builder')
    client.send('lead','response',{'intent':'ack','status':'RUNNING','slice':'S1'})
    client.close()
    d=c.process_inbox('lead').decisions[0]
    assert d.disposition=='receipt' and d.error is None
    assert not list((c.spool_root/'builder/inbox').glob('*.json'))


def test_legacy_schema_migration_does_not_assign_new_owners(tmp_path):
    import sqlite3
    path=tmp_path/'bus.sqlite';conn=sqlite3.connect(path)
    conn.executescript("CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT); INSERT INTO schema_meta VALUES('version','2'); CREATE TABLE slices(id TEXT PRIMARY KEY,to_profile TEXT,from_profile TEXT,peer TEXT,tags TEXT,title TEXT,assignment_path TEXT,content_sha256 TEXT,notify_source TEXT,status TEXT,verdict TEXT,evidence TEXT,active_job TEXT,superseded_by TEXT,created_at TEXT,updated_at TEXT); INSERT INTO slices VALUES('OLD','worker','boss','w','[]','old','/old',NULL,NULL,'RUNNING',NULL,NULL,'job',NULL,'yesterday','yesterday');")
    conn.close();store=BusStore(path)
    row=store.get_slice('OLD')
    assert row.contract=={} and row.active_job=='job' and row.from_profile=='boss'
    store.close()


def test_unknown_sender_rejected_before_registration(loop):
    c,_=loop
    with pytest.raises(ValueError,match='sender required'):
        c.assign(slice_id='S1',to_peer='builder',title='missing identity',assignment_path=str(c.team_root/'context/S1.md'))
    assert c.store.get_slice('S1') is None


def test_required_gate_count_and_binding_validation(loop):
    c, config = loop
    config['policies']['delivery']['gates'].append({'id':'engineering','responsibility':'engineering','capability':'check'})
    config['bindings']['engineering']='lead'
    (c.team_root/'workflows.json').write_text(json.dumps(config))
    assign(c);submit(c);approve(c)
    with pytest.raises(ValueError, match='engineering'):accept(c)
    c.workflow('review',actor='lead',slice_id='S1',gate='engineering',revision=1,decision='APPROVED',evidence='Code reviewed')
    accept(c)


def test_retry_assignment_uses_snapshot_even_if_binding_removed(loop):
    c, config=loop;assign(c)
    config['bindings'].clear()
    (c.team_root/'workflows.json').write_text(json.dumps(config))
    assign(c)
    assert c.store.get_slice('S1').contract['gates'][0]['peer']=='auditor'
    with pytest.raises(ValueError,match='unbound'):assign(c,sid='S2')


def test_report_only_does_not_launch(loop):
    c,_=loop
    assign(c,intent='report_only',activity='coordinate')
    assert c.process_inbox('builder').decisions[0].disposition=='report_only_complete'


def test_failed_external_delivery_stays_pending(loop,monkeypatch):
    import time
    c,_=loop;assign(c,headers={'source':'spm:request'});submit(c);approve(c)
    monkeypatch.setenv('BOT_COMS_DOORBELL','1')
    with patch('bot_coms.doorbell._adapter_runner',side_effect=OSError('network unavailable')), patch('bot_coms.doorbell._wake_runner',lambda *a:None):
        result=accept(c)
    assert result['delivery']['pending']
    assert c.store.get_slice('S1').status=='DONE'
    sent=[];future=time.time()+61
    with patch('time.time',return_value=future), patch('bot_coms.doorbell._adapter_runner',lambda *a:sent.append(a)), patch('bot_coms.doorbell._wake_runner',lambda *a:None):
        c.flush_deliveries()
    assert len(sent)==1


def test_cli_and_tool_roundtrip(loop,monkeypatch,capsys):
    from bot_coms_board.cli import main
    from hermes_bot_coms_board.tools import team_assign,team_workflow,team_inbox
    c,_=loop;monkeypatch.setenv('BOT_COMS_PEER_ID','lead')
    result=json.loads(team_assign({'slice':'S1','to_peer':'builder','title':'test','assignment_path':str(c.team_root/'context/S1.md'),'activity':'implement'}))
    assert result['success']
    monkeypatch.setenv('BOT_COMS_PEER_ID','builder')
    decision=json.loads(team_inbox())['decisions'][0]
    assert decision['lease_token']
    assert json.loads(team_workflow({'action':'describe','slice':'S1'}))['row']['contract']['return_peer']=='lead'
    assert main(['--team-root',str(c.team_root),'workflow','describe','--slice','S1','--peer','lead'])==0
    assert json.loads(capsys.readouterr().out)['row']['contract']['owner_peer']=='owner'


def test_reconcile_recovers_exit_before_running_stamp(loop,monkeypatch,tmp_path):
    from bot_coms_board.job_done import job_done
    c,_=loop;assign(c)
    home=tmp_path/'home';monkeypatch.setenv('HOME',str(home))
    sidecar=home/'.hermes/agent-screen/job.json';sidecar.parent.mkdir(parents=True)
    tee=home/'.hermes/job.out';tee.write_text('EXIT:0\n')
    sidecar.write_text(json.dumps({'slice':'S1','profile':'builder','mode':'write','out_path':str(tee),'session_id':'keep-session'}))
    assert job_done('job',team_root=c.team_root,spool_root=c.spool_root)['action']=='skip_stale_job'
    c.store.set_status('S1','RUNNING',active_job='job')
    c.reconcile()
    row=c.store.get_slice('S1')
    assert row.active_job is None and row.verdict=='EXECUTED'
    assert row.contract['submission'] is None
    assert 'keep-session' in row.evidence


def test_lost_processing_worker_is_reclaimed_and_woken(loop,monkeypatch):
    import time
    c,_=loop;assign(c)
    client=Client(c.spool_root,'builder');claim=client.claim();client.close()
    # Simulate an expired lease and due delivery retry.
    lease=c.spool_root/'builder/state/leases'/f'{claim.envelope.id}.json'
    data=json.loads(lease.read_text());data['heartbeat_at']='2000-01-01T00:00:00Z';lease.write_text(json.dumps(data))
    woke=[];future=time.time()+120
    monkeypatch.setenv('BOT_COMS_DOORBELL','1')
    with patch('time.time',return_value=future), patch('bot_coms.doorbell._wake_runner',lambda profile,peer,env:woke.append(peer)):
        c.flush_deliveries()
    assert woke==['builder']
    assert (c.spool_root/'builder/inbox'/f'{claim.envelope.id}.json').exists()


def test_new_assignment_cannot_choose_itself_as_parent(loop):
    c,_=loop
    with pytest.raises(ValueError,match='parent'):assign(c,parent_slice='S1')
    assert c.store.get_slice('S1') is None
