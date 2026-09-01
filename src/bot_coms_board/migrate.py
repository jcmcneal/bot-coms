"""One-time import from legacy BUS.md into bus.sqlite."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bot_coms_board.store import open_store, profile_to_peer, utc_iso

_HANDOFF_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_-]{0,63})\s+to:\s+(\S+)\s+from:\s+(\S+)"
    r"(?:\s+\[[^\]]+\])*\s+"
    r"(BACKLOG|QUEUED|RUNNING|BLOCKED|REVIEW|DONE|PARKED)\s+\u2014\s+(.*)$"
)
_TAG_FINDALL = re.compile(r"\[([^\]]+)\]")
_LOG_LINE = re.compile(
    r"^-\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+—\s+(.*?)(?:\s+\(actor=([^)]+)\))?\s*$"
)


def _split_sections(text: str) -> tuple[str, str, str]:
    lines = text.splitlines()
    current_i = None
    log_i = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "## Current" and current_i is None:
            current_i = i
        elif s == "## Log" and log_i is None:
            log_i = i
    if current_i is None:
        return text, "", ""
    preamble = "\n".join(lines[:current_i])
    if log_i is None:
        return preamble, "\n".join(lines[current_i + 1 :]), ""
    current = "\n".join(lines[current_i + 1 : log_i])
    log_body = "\n".join(lines[log_i + 1 :])
    return preamble, current, log_body


def parse_current_slices(current_body: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    lines = current_body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HANDOFF_RE.match(line.strip())
        if not m:
            i += 1
            continue
        slice_id = m.group(1)
        to_profile = m.group(2)
        from_profile = m.group(3)
        status = m.group(4)
        title = m.group(5).strip()
        tags = _TAG_FINDALL.findall(line)
        i += 1
        fields: dict[str, str] = {}
        while i < len(lines):
            if not lines[i].strip():
                i += 1
                if i < len(lines) and _HANDOFF_RE.match(lines[i].strip()):
                    break
                continue
            if _HANDOFF_RE.match(lines[i].strip()):
                break
            stripped = lines[i].strip()
            for key in ("Assignment", "Evidence", "Ack", "Verdict"):
                if stripped.startswith(f"{key}:"):
                    fields[key] = stripped[len(key) + 1 :].strip()
                    break
            i += 1
        assignment = fields.get("Assignment", "")
        blocks.append(
            {
                "id": slice_id,
                "to_profile": to_profile,
                "from_profile": from_profile,
                "peer": profile_to_peer(to_profile),
                "tags": tags,
                "title": title,
                "assignment_path": assignment,
                "content_sha256": None,
                "status": status,
                "verdict": fields.get("Verdict") if fields.get("Verdict") != "none" else None,
                "evidence": fields.get("Evidence"),
                "active_job": None,
                "superseded_by": None,
            }
        )
    return blocks


def parse_log_lines(log_body: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in log_body.splitlines():
        m = _LOG_LINE.match(line.strip())
        if not m:
            continue
        entries.append(
            {
                "at": m.group(1),
                "message": m.group(2).strip(),
                "actor": (m.group(3) or "unknown").strip(),
            }
        )
    return entries


def migrate_bus_md(
    bus_path: Path,
    *,
    team_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    from bot_coms_board.store import team_root_from_env

    root = team_root or team_root_from_env()
    text = bus_path.read_text(encoding="utf-8")
    preamble, current, log_body = _split_sections(text)
    slices = parse_current_slices(current)
    logs = parse_log_lines(log_body)
    report: dict[str, Any] = {
        "slices_found": len(slices),
        "log_entries": len(logs),
        "dry_run": dry_run,
    }
    if dry_run:
        report["slices"] = slices
        return report
    store = open_store(team_root=root)
    now = utc_iso()
    for s in slices:
        if not s.get("assignment_path"):
            s["assignment_path"] = str(root / "context" / f"{s['id']}.md")
        s.setdefault("created_at", now)
        s.setdefault("updated_at", now)
        store.upsert_slice_raw(s)
    for entry in logs:
        store.append_log(entry["actor"], entry["message"], at=entry["at"])
    if "## HOLD" in preamble or "HOLD" in preamble[:500]:
        store.set_setting("global_hold_note", preamble[:4000])
    report["imported_slices"] = len(slices)
    report["imported_log"] = len(logs)
    return report


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Import legacy BUS.md into bus.sqlite")
    parser.add_argument(
        "--bus",
        default=str(Path.home() / ".hermes" / "team" / "BUS.md"),
    )
    parser.add_argument(
        "--team-root",
        default="",
        help="Team data root (default ~/.hermes/team)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    team_root = Path(args.team_root).expanduser() if args.team_root.strip() else None
    report = migrate_bus_md(
        Path(args.bus),
        team_root=team_root,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
