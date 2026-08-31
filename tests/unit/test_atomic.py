from __future__ import annotations

from pathlib import Path

import pytest

from bot_coms.atomic import read_json, set_fault_hook, write_json_atomic


def test_write_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "inbox"
    tmp = tmp_path / "tmp"
    dest.mkdir()
    tmp.mkdir()
    path = write_json_atomic(dest, "hello.json", {"a": 1}, tmp_dir=tmp, root=tmp_path)
    assert path.is_file()
    assert read_json(path) == {"a": 1}
    assert list(tmp.glob("*.partial")) == []


def test_fault_before_rename_cleans_partial(tmp_path: Path) -> None:
    dest = tmp_path / "inbox"
    tmp = tmp_path / "tmp"
    dest.mkdir()
    tmp.mkdir()

    def boom(stage: str) -> None:
        if stage == "before_rename":
            raise OSError("injected")

    set_fault_hook(boom)
    try:
        with pytest.raises(OSError):
            write_json_atomic(dest, "hello.json", {"a": 1}, tmp_dir=tmp, root=tmp_path)
    finally:
        set_fault_hook(None)
    assert list(dest.glob("*.json")) == []
    assert list(tmp.glob("*")) == []
