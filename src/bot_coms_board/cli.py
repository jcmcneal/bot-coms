"""bot-coms-board CLI — team coordination on bus.sqlite."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from bot_coms.headers import resolve_assign_headers
from bot_coms_board.coordinator import TeamCoordinator, default_spool_root
from bot_coms_board.handler import run_worker
from bot_coms_board.migrate import migrate_bus_md
from bot_coms_board.store import open_store, team_root_from_env
from bot_coms_board.tool import handle_team_bus


def _json_print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _team_root(ns: argparse.Namespace) -> Path:
    if ns.team_root:
        return Path(ns.team_root).expanduser()
    return team_root_from_env()


def _spool(ns: argparse.Namespace) -> Path:
    if ns.spool_root:
        return Path(ns.spool_root).expanduser()
    return default_spool_root()


def _coord(ns: argparse.Namespace) -> TeamCoordinator:
    return TeamCoordinator(team_root=_team_root(ns), spool_root=_spool(ns))


def cmd_register(ns: argparse.Namespace) -> int:
    coord = _coord(ns)
    row = coord.register_slice(
        slice_id=ns.slice,
        title=ns.title,
        assignment_path=ns.assignment_path,
        to_profile=ns.to_profile,
        from_profile=ns.from_profile,
        peer=ns.peer,
        tags=[t.strip() for t in ns.tags.split(",") if t.strip()] if ns.tags else None,
    )
    _json_print({"success": True, "row": row})
    return 0


def cmd_assign(ns: argparse.Namespace) -> int:
    coord = _coord(ns)
    from_peer = os.environ.get("BOT_COMS_PEER_ID", "").strip() or None
    try:
        result = coord.assign(
            slice_id=ns.slice,
            to_peer=ns.to_peer,
            title=ns.title,
            assignment_path=ns.assignment_path,
            from_profile=ns.from_profile,
            from_peer=from_peer,
            to_profile=ns.to_profile or None,
            tags=[t.strip() for t in ns.tags.split(",") if t.strip()] if ns.tags else None,
            intent=ns.intent,
            headers=resolve_assign_headers(None),
        )
    except Exception as exc:
        _json_print({"success": False, "error": str(exc)})
        return 1
    _json_print(
        {
            "success": True,
            "slice": result.slice_id,
            "row": result.row,
            "outbound_id": result.outbound_id,
            "correlation_id": result.correlation_id,
        }
    )
    return 0


def cmd_inbox(ns: argparse.Namespace) -> int:
    coord = _coord(ns)
    result = coord.process_inbox(
        ns.peer,
        limit=ns.limit,
        auto_handle=ns.auto_handle,
    )
    _json_print(
        {
            "success": True,
            "peer": result.peer,
            "reclaimed": result.reclaimed,
            "decisions": [
                {
                    "message_id": d.message_id,
                    "slice": d.slice_id,
                    "intent": d.intent,
                    "disposition": d.disposition,
                    "handled": d.handled,
                    "slice_row": d.slice_row,
                    "assignment_path": d.assignment_path,
                    "assignment_body": d.assignment_body,
                    "ack_result": d.ack_result,
                    "error": d.error,
                    "error_code": d.error_code,
                }
                for d in result.decisions
            ],
        }
    )
    return 0


def cmd_verdict(ns: argparse.Namespace) -> int:
    store = open_store(team_root=_team_root(ns))
    row = store.set_verdict(ns.slice, verdict=ns.verdict, evidence=ns.evidence)
    if row is None:
        _json_print({"success": False, "error": f"slice not found: {ns.slice}"})
        return 1
    _json_print({"success": True, "row": row.to_dict()})
    return 0


def cmd_report(ns: argparse.Namespace) -> int:
    coord = _coord(ns)
    try:
        out = coord.report(
            slice_id=ns.slice,
            verdict=ns.verdict,
            evidence=ns.evidence,
            from_peer=ns.from_peer or os.environ.get("BOT_COMS_PEER_ID", ""),
        )
    except Exception as exc:
        _json_print({"success": False, "error": str(exc)})
        return 1
    _json_print({"success": True, **out})
    return 0


def cmd_status(ns: argparse.Namespace) -> int:
    raw = handle_team_bus(
        {"action": "status", "slice": ns.slice, "status": ns.set_status, "active_job": ns.active_job},
        actor=ns.actor,
    )
    sys.stdout.write(raw + "\n")
    return 0 if json.loads(raw).get("success") else 1


def cmd_slice(ns: argparse.Namespace) -> int:
    raw = handle_team_bus({"action": "slice", "slice": ns.slice})
    sys.stdout.write(raw + "\n")
    return 0 if json.loads(raw).get("success") else 1


def cmd_list(ns: argparse.Namespace) -> int:
    raw = handle_team_bus({"action": "list", "limit": ns.limit})
    sys.stdout.write(raw + "\n")
    return 0 if json.loads(raw).get("success") else 1


def cmd_migrate(ns: argparse.Namespace) -> int:
    report = migrate_bus_md(Path(ns.bus), team_root=_team_root(ns), dry_run=ns.dry_run)
    _json_print(report)
    return 0


def cmd_worker(ns: argparse.Namespace) -> int:
    summary = run_worker(
        ns.peer,
        team_root=_team_root(ns),
        spool_root=_spool(ns),
        idle_rounds=ns.idle_rounds,
    )
    _json_print(summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bot-coms-board")
    parser.add_argument(
        "--team-root",
        default="",
        help="Team data root (default ~/.hermes/team or BOT_COMS_TEAM_ROOT)",
    )
    parser.add_argument("--spool-root", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register")
    p.add_argument("--slice", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--assignment-path", required=True)
    p.add_argument("--to-profile", default="software-engineer")
    p.add_argument("--from-profile", default="project-manager")
    p.add_argument("--peer", default="")
    p.add_argument("--tags", default="")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("assign")
    p.add_argument("--slice", required=True)
    p.add_argument("--to-peer", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--assignment-path", required=True)
    p.add_argument("--from-profile", default="project-manager")
    p.add_argument("--to-profile", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--intent", default="assign")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("inbox")
    p.add_argument("--peer", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--auto-handle", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("verdict")
    p.add_argument("--slice", required=True)
    p.add_argument("--verdict", default="")
    p.add_argument("--evidence", default="")
    p.set_defaults(func=cmd_verdict)

    p = sub.add_parser("report")
    p.add_argument("--slice", required=True)
    p.add_argument("--from-peer", default="")
    p.add_argument("--verdict", default="")
    p.add_argument("--evidence", default="")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("status")
    p.add_argument("--slice", required=True)
    p.add_argument("--set-status", dest="set_status", required=True)
    p.add_argument("--active-job", default="")
    p.add_argument("--actor", default="project-manager")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("slice")
    p.add_argument("--slice", required=True)
    p.set_defaults(func=cmd_slice)

    p = sub.add_parser("list")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("migrate")
    p.add_argument("--bus", default=str(Path.home() / ".hermes" / "team" / "BUS.md"))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("worker")
    p.add_argument("--peer", required=True)
    p.add_argument("--idle-rounds", type=int, default=3)
    p.set_defaults(func=cmd_worker)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
