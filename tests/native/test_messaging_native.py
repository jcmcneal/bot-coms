import asyncio
import json

import pytest
native_tests = pytest.importorskip('tests.tui_gateway.test_plugin_sessions')
runtime = native_tests.runtime
from tui_gateway.plugin_sessions import get_session_service
from bot_coms_messaging.service import MessagingService


def test_messaging_real_native_session_reuse(runtime):
    _, host, calls, home = runtime
    (home / 'config.yaml').write_text('{"plugins":{"enabled":["bot-coms","bot-coms-messaging"]}}')
    root = home / 'plugin-data' / 'bot-coms-messaging'
    root.mkdir(parents=True)
    (root / 'config.json').write_text(json.dumps(dict(server_id='server', profiles=[
        dict(id='default-id', name='default', peer='default', enabled=True, principals=['test:alice'])])))
    backend = MessagingService(root, get_session_service('bot-coms-messaging'))
    # Fixture uses an inline thread class to exercise the real handler thread body;
    # call this tiny facade directly instead of asyncio's thread pool in this test.
    async def call(method, **kwargs):
        return getattr(backend.runtime, method)(**kwargs)
    backend.call = call
    backend.acquire()
    try:
        first = backend.store.send('test:alice', 'one', 'hello one', [], dm=('default-id', 'Default'))
        asyncio.run(backend.tick())
        asyncio.run(backend.tick())
        history = backend.store.history('test:alice', first['conversation']['id'])
        assert history['runs'][0]['status'] == 'completed', history
        assert history['messages'][-1]['body'].startswith('reply:')
        backend.store.send('test:alice', 'two', 'hello two', [], dm=('default-id', 'Default'))
        asyncio.run(backend.tick())
        asyncio.run(backend.tick())
        assert len(calls) == 2
        assert calls[0]['session_id'] == calls[1]['session_id']
        assert 'hello one' not in calls[1]['text']
        assert 'hello two' in calls[1]['text']
        assert 'hello one' in str(calls[1]['history'])
        assert calls[0]['max_turns'] == 12
        assert len(backend.store.history('test:alice', first['conversation']['id'])['messages']) == 4
    finally:
        backend.release()


def make_backend(home):
    (home / 'config.yaml').write_text('{"plugins":{"enabled":["bot-coms","bot-coms-messaging"]}}')
    root = home / 'plugin-data' / 'bot-coms-messaging'
    root.mkdir(parents=True)
    (root / 'config.json').write_text(json.dumps(dict(server_id='server', profiles=[
        dict(id='default-id', name='default', peer='default', enabled=True, principals=['test:alice'])])))
    backend = MessagingService(root, get_session_service('bot-coms-messaging'))
    async def call(method, **kwargs):
        return getattr(backend.runtime, method)(**kwargs)
    backend.call = call
    backend.acquire()
    return backend


def test_native_rejected_busy_admission_requeues_without_poisoning_session(runtime, monkeypatch):
    from tui_gateway.plugin_sessions import SessionServiceConflict
    _, _, calls, home = runtime
    backend = make_backend(home)
    try:
        first = backend.store.send('test:alice', 'one', 'hello', [], dm=('default-id', 'Default'))
        original = backend.runtime.submit
        def busy(**kwargs):
            raise SessionServiceConflict('native session busy before admission')
        monkeypatch.setattr(backend.runtime, 'submit', busy)
        asyncio.run(backend.tick())
        assert backend.store.history('test:alice', first['conversation']['id'])['runs'][0]['status'] == 'queued'
        assert not calls
        monkeypatch.setattr(backend.runtime, 'submit', original)
        asyncio.run(backend.tick())
        asyncio.run(backend.tick())
        assert len(calls) == 1
        assert backend.store.history('test:alice', first['conversation']['id'])['runs'][0]['status'] == 'completed'
    finally:
        backend.release()


def test_native_terminal_failure_allows_new_instruction_without_replaying_old_turn(runtime, monkeypatch):
    from tui_gateway import server
    _, _, calls, home = runtime
    backend = make_backend(home)
    try:
        first = backend.store.send('test:alice', 'one', 'hello', [], dm=('default-id', 'Default'))
        original = server._wait_agent_for_prompt
        monkeypatch.setattr(server, '_wait_agent_for_prompt', lambda *a: {'error': {'message': 'build failed'}})
        asyncio.run(backend.tick())
        asyncio.run(backend.tick())
        assert backend.store.history('test:alice', first['conversation']['id'])['runs'][0]['status'] == 'needs_attention'
        monkeypatch.setattr(server, '_wait_agent_for_prompt', original)
        backend.store.send('test:alice', 'two', 'new instruction', [], dm=('default-id', 'Default'))
        asyncio.run(backend.tick())
        asyncio.run(backend.tick())
        assert len(calls) == 1
        assert 'new instruction' in calls[0]['text']
        assert len(backend.store.history('test:alice', first['conversation']['id'])['messages']) == 3
    finally:
        backend.release()
