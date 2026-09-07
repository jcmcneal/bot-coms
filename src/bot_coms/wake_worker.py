"""Serialized, crash-recoverable peer wake runner; no polling when idle."""
from __future__ import annotations

import fcntl
import subprocess
import sys
from pathlib import Path


def start(directory: Path, profile: str, hermes: str) -> None:
    subprocess.Popen(
        [sys.executable, '-m', 'bot_coms.wake_worker', str(directory), profile, hermes],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def recover(team: Path) -> None:
    from bot_coms.doorbell import doorbell_enabled, hermes_bin, peer_to_hermes_profile
    if not doorbell_enabled():
        return
    for directory in (team / 'wake-state').glob('*'):
        if not directory.is_dir() or not any(directory.glob('*.txt')):
            continue
        with (directory / 'session.lock').open('a+b') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            start(directory, peer_to_hermes_profile(directory.name), hermes_bin())


def drain(directory: Path, profile: str, hermes: str) -> None:
    # The child inherits the lease: killing this supervisor cannot permit a
    # second session while its Hermes child is still running.
    with (directory / 'session.lock').open('a+b') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        while True:
            queries = sorted(directory.glob('*.txt'))
            if not queries:
                return
            query = queries[0]
            command = [hermes]
            if profile != 'default':
                command += ['-p', profile]
            command += ['chat', '--in', '~', '-Q', '--query-file', str(query)]
            result = subprocess.run(command, pass_fds=(lease.fileno(),), check=False)
            if result.returncode:
                # Leave durable work for the next delivery/recovery attempt.
                return
            query.unlink(missing_ok=True)


if __name__ == '__main__':
    drain(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
