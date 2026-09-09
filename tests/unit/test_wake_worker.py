"""Retired wake entry points must never start a process."""
import subprocess

import pytest

from bot_coms.wake_worker import drain, recover, start


def test_legacy_entrypoints_cannot_launch(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('standalone wake launched a process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    for entry in (start, drain):
        with pytest.raises(RuntimeError, match='retired'):
            entry(tmp_path, 'worker', 'hermes')
    recover(tmp_path)
