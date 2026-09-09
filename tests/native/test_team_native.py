"""Optional cross-repository checks against the matching Hermes source checkout."""
import asyncio
import json
import sqlite3
from contextlib import closing

import pytest

native_tests = pytest.importorskip('tests.tui_gateway.test_plugin_sessions')
runtime = native_tests.runtime

from bot_coms import Client, init_spool
from bot_coms.team_runtime import TeamRuntime
from hermes_bot_coms.tools import bot_coms_ack, bot_coms_claim
from tui_gateway import plugin_sessions, server


def test_team_native_turn_reuses_session_and_claims_as_correct_peer(runtime, monkeypatch):
    _, host, calls, home = runtime
    # The shared Hermes fixture runs handler threads inline; use the same policy
    # for scanner threads so this test exercises deterministic real I/O.
    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    profile_home = home / 'profiles' / 'work'
    profile_home.mkdir(parents=True)
    profile_home.joinpath('config.yaml').write_text(json.dumps({
        'plugins': {'enabled': ['bot-coms', 'bot-coms-board']}}))
    team = home / 'team'
    init_spool(team / 'spool', ['sender', 'work'])
    monkeypatch.setenv('BOT_COMS_PEER_ID', 'wrong-process-actor')
    monkeypatch.setenv('BOT_COMS_SPOOL_ROOT', '/does-not-exist')
    original_build = server._start_agent_build
    handled = []

    def build(sid, record):
        original_build(sid, record)
        agent = record['agent']
        if getattr(agent, '_handles_bot_coms', False):
            return
        run = agent.run_conversation
        def handle(text, **kwargs):
            claimed = json.loads(bot_coms_claim())
            assert claimed['claimed'], claimed
            envelope = claimed['envelope']
            handled.append(envelope['id'])
            bot_coms_ack({'id': envelope['id'], 'lease_token': claimed['lease_token'],
                          'result': {'received': True}})
            return run(text, **kwargs)
        agent.run_conversation = handle
        agent._handles_bot_coms = True
    monkeypatch.setattr(server, '_start_agent_build', build)

    native = plugin_sessions.get_session_service('bot-coms')
    service = TeamRuntime(team_root=team, executor=native)
    try:
        for body in ('first request', 'follow up'):
            with closing(Client(team / 'spool', 'sender')) as sender:
                env = sender.send('work', 'request', {'body': body}, correlation_id='shared-task')
            result = asyncio.run(service.tick())
            assert not result['errors'], result
            assert handled[-1] == env.id
            with closing(Client(team / 'spool', 'work')) as receiver:
                assert receiver.status(env.id).location == 'acked'
        assert len(calls) == 2
        assert calls[0]['session_id'] == calls[1]['session_id']
        assert all(call['peer'] == 'work' for call in calls)
        assert calls[1]['history'][-1]['role'] == 'assistant'
        service.close()
        restored = TeamRuntime(team_root=team, executor=native)
        try:
            asyncio.run(restored.tick())
            assert len(calls) == 2
            with sqlite3.connect(team / 'team-runtime.sqlite3') as db:
                assert db.execute("SELECT count(*) FROM turns WHERE status='completed'").fetchone()[0] == 2
        finally:
            restored.close()
    finally:
        service.close()
