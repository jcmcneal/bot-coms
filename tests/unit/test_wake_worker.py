from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

from bot_coms.wake_worker import drain


def test_concurrent_wakes_serialize_and_drain_arrivals(tmp_path, monkeypatch):
    started, release = Event(), Event()
    mutex = Lock()
    calls = []
    active = 0

    def run(command, **kwargs):
        nonlocal active
        with mutex:
            active += 1
            assert active == 1
            calls.append(command[-1])
        assert kwargs['pass_fds']
        started.set()
        assert release.wait(5)
        with mutex:
            active -= 1
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('bot_coms.wake_worker.subprocess.run', run)
    (tmp_path / 'one.txt').write_text('first')
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(drain, tmp_path, 'peer', 'hermes')
        assert started.wait(5)
        (tmp_path / 'two.txt').write_text('arrived while running')
        second = pool.submit(drain, tmp_path, 'peer', 'hermes')
        release.set()
        first.result(5)
        second.result(5)
    assert len(calls) == 2
    assert not list(tmp_path.glob('*.txt'))


def test_failed_session_retains_pending_work_for_retry(tmp_path, monkeypatch):
    query = tmp_path / 'pending.txt'
    query.write_text('retry me')
    monkeypatch.setattr('bot_coms.wake_worker.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=1))
    drain(tmp_path, 'peer', 'hermes')
    assert query.exists()
    monkeypatch.setattr('bot_coms.wake_worker.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0))
    drain(tmp_path, 'peer', 'hermes')
    assert not query.exists()
