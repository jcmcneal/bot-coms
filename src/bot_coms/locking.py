"""Reentrant thread + process locks for a peer's claim/lease transitions."""
from contextlib import contextmanager
from functools import wraps
import fcntl
import threading

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def peer_lock(paths):
    key = str(paths.peer_root.resolve())
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, 'held', {})
        if key in held:
            yield
            return
        path = paths.state / 'lifecycle.lock'
        with path.open('a+b') as f:
            path.chmod(0o600)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            _local.held = {**held, key: True}
            try:
                yield
            finally:
                _local.held = held
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def locked(function):
    @wraps(function)
    def wrapped(paths, *args, **kwargs):
        with peer_lock(paths):
            return function(paths, *args, **kwargs)
    return wrapped
