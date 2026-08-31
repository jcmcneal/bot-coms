from __future__ import annotations

from pathlib import Path


def test_no_forbidden_imports() -> None:
    roots = [
        Path("src"),
        Path("adapters"),
    ]
    needles = ("BUS.md", "plugins/platforms/a2a", "open-genome")
    hits: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    hits.append(f"{path}: {needle}")
    assert hits == []
