"""Headless bot-coms worker handler for team coordination."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bot_coms.types import ClaimedMessage, SkipMessage

from bot_coms_board.coordinator import TeamCoordinator, default_spool_root

_WORKER_INTENTS = frozenset({"report_only", "report", "cancel"})


def make_team_handler(
    *,
    peer: str,
    team_root: Path | None = None,
    spool_root: Path | None = None,
) -> Any:
    """Return a handler suitable for ``bot_coms.Worker``.

    Handles deterministic intents only (report_only, report, cancel).
    Assign is wake-only via pulse; SWE completes via team_inbox + bot_coms_ack.
    """

    coord = TeamCoordinator(
        team_root=team_root,
        spool_root=spool_root or default_spool_root(),
    )

    def handler(claimed: ClaimedMessage) -> dict[str, Any] | None:
        from bot_coms import Client

        client = Client(coord.spool_root, peer)
        try:
            decision = coord.process_claim(
                claimed,
                client=client,
                auto_handle=True,
                auto_handle_intents=_WORKER_INTENTS,
            )
        finally:
            client.close()

        if decision.disposition == "launch_cursor":
            raise SkipMessage()
        return decision.ack_result if decision.handled else None

    return handler


def run_worker(
    peer: str,
    *,
    team_root: Path | None = None,
    spool_root: Path | None = None,
    poll_interval_s: float | None = None,
    idle_rounds: int = 3,
) -> dict[str, Any]:
    """Process inbox once (for cron/pulse). Returns summary JSON."""
    from bot_coms import Client, Worker

    root = spool_root or default_spool_root()
    client = Client(root, peer)
    handler = make_team_handler(peer=peer, team_root=team_root, spool_root=root)
    worker = Worker(client, handler, poll_interval_s=poll_interval_s)
    worker.run_until_idle(idle_rounds=idle_rounds)
    client.close()
    return {"peer": peer, "status": "idle"}


def build_assign_wake_query(
    *,
    slice_id: str,
    assignment_path: str,
    assignment_body: str,
    body_preview_chars: int = 4000,
) -> str:
    """Minimal Hermes wake query for an assign doorbell."""
    preview = assignment_body[:body_preview_chars]
    return (
        f"Slice {slice_id} assigned.\n"
        f"Assignment: {assignment_path}\n\n"
        f"{preview}\n\n"
        "Call team_inbox, then launch ONE cursor_screen job for this slice. "
        "Stamp team_bus status RUNNING with active_job, then bot_coms_ack. End turn after launch."
    )
