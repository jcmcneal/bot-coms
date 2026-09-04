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
    Assignments are handled by Hermes via team_inbox; recovery uses the durable relay.
    """

    coord = TeamCoordinator(
        team_root=team_root,
        spool_root=spool_root or default_spool_root(),
    )

    def handler(claimed: ClaimedMessage) -> dict[str, Any] | None:
        if claimed.envelope.type == "response":
            return None
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

        if decision.disposition in {"launch_agent", "coordinate"}:
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
        "Call team_inbox and follow the assignment activity and frozen workflow contract. "
        "Coordinate in Hermes; launch an agent only when the explicit activity needs one. "
        "For agent work, stamp RUNNING with active_job and acknowledge with the claim token. "
        "End turn after dispatch."
    )
