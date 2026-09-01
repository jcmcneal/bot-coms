"""Import boundary: bot_coms core must never import bot_coms_board."""

from __future__ import annotations

import ast
from pathlib import Path


def _imports_bot_coms_board(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "bot_coms_board" or alias.name.startswith("bot_coms_board."):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "bot_coms_board" or node.module.startswith("bot_coms_board.")
            ):
                hits.append(node.module)
    return hits


def test_bot_coms_core_never_imports_board() -> None:
    core_root = Path("src/bot_coms")
    hits: list[str] = []
    for path in core_root.rglob("*.py"):
        for mod in _imports_bot_coms_board(path):
            hits.append(f"{path}: {mod}")
    assert hits == []
