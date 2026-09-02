"""Allowlisted atomic writes for PM-owned team constitution files."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from bot_coms_board.store import team_root_from_env

WriteMode = Literal["replace", "append", "upsert_section"]
_MODES = frozenset({"replace", "append", "upsert_section"})
_SECTION_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

_FAULT: Callable[[str], None] | None = None


def set_fault_hook(hook: Callable[[str], None] | None) -> None:
    global _FAULT
    _FAULT = hook


def _fault(stage: str) -> None:
    hook = _FAULT
    if hook is not None:
        hook(stage)


def allowed_doc_paths(*, team_root: Path | None = None) -> frozenset[Path]:
    root = team_root if team_root is not None else team_root_from_env()
    return frozenset(
        {
            (root.parent / "TEAM.md").resolve(),
            (root / "STANDING.md").resolve(),
        }
    )


def resolve_allowed_path(raw: str, *, team_root: Path | None = None) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path is required")
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ValueError(f"path not allowed: {raw}") from exc
    allow = allowed_doc_paths(team_root=team_root)
    if resolved not in allow:
        raise ValueError(f"path not allowed: {raw}")
    return resolved


def upsert_section(text: str, section: str, body: str) -> str:
    target = section.strip()
    if not target:
        raise ValueError("section must be non-empty")
    content = body.rstrip("\n") + "\n"
    lines = text.splitlines(keepends=True) if text else []
    start: int | None = None
    heading_prefix = "##"
    for i, line in enumerate(lines):
        m = _SECTION_HEADING_RE.match(line.rstrip("\n\r"))
        if m and m.group(2).strip() == target:
            start = i
            heading_prefix = m.group(1)
            break
    if start is None:
        base = text.rstrip("\n")
        block = f"{heading_prefix} {target}\n{content}"
        return f"{base}\n\n{block}" if base else block
    start_level = len(heading_prefix)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _SECTION_HEADING_RE.match(lines[j].rstrip("\n\r"))
        if m and len(m.group(1)) <= start_level:
            end = j
            break
    merged = lines[: start + 1] + [content] + lines[end:]
    return "".join(merged)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.partial"
    )
    try:
        partial.write_text(text, encoding="utf-8")
        _fault("before_rename")
        os.replace(partial, path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def write_doc(
    *,
    path: str,
    content: str,
    mode: WriteMode = "replace",
    section: str | None = None,
    team_root: Path | None = None,
) -> dict[str, str | int]:
    if mode not in _MODES:
        raise ValueError(f"invalid mode: {mode}")
    if not isinstance(content, str):
        raise ValueError("content is required")
    dest = resolve_allowed_path(path, team_root=team_root)
    if mode == "upsert_section":
        if dest.name != "TEAM.md":
            raise ValueError("upsert_section is only allowed for TEAM.md")
        if not isinstance(section, str) or not section.strip():
            raise ValueError("upsert_section requires section")
        existing = dest.read_text(encoding="utf-8") if dest.is_file() else ""
        text = upsert_section(existing, section, content)
    elif mode == "append":
        existing = dest.read_text(encoding="utf-8") if dest.is_file() else ""
        text = existing + content
    else:
        text = content
    write_text_atomic(dest, text)
    return {"path": str(dest), "mode": mode, "bytes": len(text.encode("utf-8"))}
