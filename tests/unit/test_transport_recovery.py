import threading
from unittest.mock import patch
import pytest
from bot_coms import Worker
from bot_coms import lifecycle
from bot_coms.types import ClaimLost


def test_stale_claim_cannot_ack_new_lease(clients,clock,config):
    a,b=clients;a.send('b','request',{'x':1});old=b.claim()
    clock.advance(config.lease_timeout_s+1);new=b.claim()
    assert old.lease_token!=new.lease_token
    with pytest.raises(ClaimLost):b.ack(old)
    b.ack(new)


def test_claim_and_lease_are_atomic_to_reclaimer(clients):
    a,b=clients;e=a.send('b','request',{'x':1})
    entered=threading.Event();release=threading.Event();results=[]
    original=lifecycle.write_lease
    def pause(*args,**kw):
        entered.set();assert release.wait(2);return original(*args,**kw)
    with patch.object(lifecycle,'write_lease',pause):
        t=threading.Thread(target=lambda:results.append(b.claim()));t.start();assert entered.wait(2)
        r=threading.Thread(target=lambda:results.append(b.reclaim_stale()));r.start()
        release.set();t.join(3);r.join(3)
    assert any(x==[] for x in results)
    assert (b.paths.processing/f'{e.id}.json').exists()


def test_result_publication_failure_replays_without_handler(clients,clock,config):
    a,b=clients;e=a.send('b','request',{'x':1});claimed=b.claim()
    with patch.object(lifecycle,'write_result',side_effect=OSError('crash')):
        with pytest.raises(OSError):b.ack(claimed,result={'done':True})
    assert claimed.path.exists()
    clock.advance(config.lease_timeout_s+1);retried=b.claim()
    calls=[];Worker(b,lambda c:calls.append(c)).handle_one(retried)
    assert calls==[] and a.poll_result(e.correlation_id).payload=={'done':True}


def test_worker_skips_old_assign_and_handles_later_mail(clients):
    from bot_coms.types import SkipMessage
    a,b=clients;a.send('b','request',{'intent':'assign'});a.send('b','request',{'intent':'report_only'})
    seen=[]
    def handler(c):
        if c.envelope.payload['intent']=='assign':raise SkipMessage()
        seen.append(c.envelope.payload['intent'])
    Worker(b,handler,poll_interval_s=0).run_until_idle(idle_rounds=1)
    assert seen==['report_only']


def test_worker_batch_read_cost(clients):
    a,b=clients
    for n in range(40):a.send('b','event',{'n':n})
    with patch.object(lifecycle,'_read_envelope',wraps=lifecycle._read_envelope) as reads:
        Worker(b,lambda c:None,poll_interval_s=0).run_until_idle(idle_rounds=1)
    assert reads.call_count==40
