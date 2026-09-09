import time

from bot_coms_runtime.cli_sessions import CliSessionRuntime


class Process:
    next_pid = 10000

    def __init__(self, argv, **_):
        self.argv = argv
        self.pid = Process.next_pid
        Process.next_pid += 1
        self.returncode = 0

    def communicate(self):
        return 'reply', '\nsession_id: session-1\n'

    def poll(self):
        return self.returncode


def settle(runtime, principal, operation):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        receipt = runtime.status(principal_id=principal, operation_key=operation)
        if receipt and not receipt['active']:
            return receipt
        time.sleep(.01)
    raise AssertionError('CLI turn did not settle')


def test_plugin_runtime_reuses_the_cli_session_id(tmp_path, monkeypatch):
    calls = []

    def popen(argv, **kwargs):
        calls.append(argv)
        return Process(argv, **kwargs)

    monkeypatch.setattr('bot_coms_runtime.cli_sessions.subprocess.Popen', popen)
    runtime = CliSessionRuntime(tmp_path, 'bot-coms-messaging')
    monkeypatch.setattr(runtime, '_hermes', lambda: '/fake/hermes')

    runtime.submit(principal_id='account:alice', profile='default', conversation_key='dm:1',
                   operation_key='one', text='first', max_turns=3)
    assert settle(runtime, 'account:alice', 'one')['result'] == {'text': 'reply'}
    binding = runtime.ensure_session(principal_id='account:alice', profile='default', conversation_key='dm:1')
    assert binding['session_id'] == 'session-1'

    runtime.submit(principal_id='account:alice', profile='default', conversation_key='dm:1',
                   operation_key='two', text='second')
    settle(runtime, 'account:alice', 'two')
    assert '--resume' in calls[1]
    assert calls[1][calls[1].index('--resume') + 1] == 'session-1'
