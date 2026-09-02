"""Compose board (bus.sqlite) + bot-coms spool into high-level coordination."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot_coms import Client
from bot_coms.headers import SOURCE_HEADER
from bot_coms.types import ClaimedMessage

from bot_coms_board.payload import (
    PayloadError,
    SlicePayload,
    parse_payload,
    sha256_assignment_spec,
    verify_content_digest,
)
from bot_coms_board.slice_status import merge_slice_view
from bot_coms_board.store import BusStore, open_store, profile_to_peer, team_root_from_env


def default_spool_root() -> Path:
    raw = os.environ.get("BOT_COMS_SPOOL_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return team_root_from_env() / "spool"


def sha256_file(path: Path) -> str:
    return sha256_assignment_spec(path)


def _notify_source_from_headers(headers: dict[str, str] | None) -> str | None:
    if not headers:
        return None
    source = (headers.get(SOURCE_HEADER) or "").strip()
    return source or None


def _report_headers(notify_source: str | None) -> dict[str, str] | None:
    if not notify_source:
        return None
    return {SOURCE_HEADER: notify_source}


def read_assignment_body(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


@dataclass
class InboxDecision:
    message_id: str
    slice_id: str
    intent: str
    disposition: str
    slice_row: dict[str, Any] | None = None
    assignment_path: str | None = None
    assignment_body: str | None = None
    ack_result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    handled: bool = False


@dataclass
class AssignResult:
    slice_id: str
    row: dict[str, Any]
    correlation_id: str
    outbound_id: str


@dataclass
class InboxResult:
    peer: str
    reclaimed: list[str] = field(default_factory=list)
    decisions: list[InboxDecision] = field(default_factory=list)


class TeamCoordinator:
    """High-level team coordination composing board + spool."""

    def __init__(
        self,
        *,
        team_root: Path | None = None,
        spool_root: Path | None = None,
        store: BusStore | None = None,
    ) -> None:
        self.team_root = team_root or team_root_from_env()
        self.spool_root = spool_root or default_spool_root()
        self.store = store or open_store(team_root=self.team_root)

    def register_slice(
        self,
        *,
        slice_id: str,
        title: str,
        assignment_path: str,
        to_profile: str,
        from_profile: str,
        peer: str | None = None,
        tags: list[str] | None = None,
        notify_source: str | None = None,
    ) -> dict[str, Any]:
        path = Path(assignment_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"assignment file not found: {path}")
        digest = sha256_file(path)
        row = self.store.register_slice(
            slice_id=slice_id,
            to_profile=to_profile,
            from_profile=from_profile,
            peer=peer or profile_to_peer(to_profile),
            title=title,
            assignment_path=str(path),
            content_sha256=digest,
            tags=tags or [],
            status="QUEUED",
            notify_source=notify_source,
        )
        return row.to_dict()

    def assign(
        self,
        *,
        slice_id: str,
        to_peer: str,
        title: str,
        assignment_path: str,
        from_profile: str = "project-manager",
        to_profile: str | None = None,
        tags: list[str] | None = None,
        intent: str = "assign",
        headers: dict[str, str] | None = None,
    ) -> AssignResult:
        if to_profile is None:
            from bot_coms_board.store import peer_to_profile

            to_profile = peer_to_profile(to_peer)
        notify_source = _notify_source_from_headers(headers)
        row = self.register_slice(
            slice_id=slice_id,
            title=title,
            assignment_path=assignment_path,
            to_profile=to_profile,
            from_profile=from_profile,
            peer=to_peer,
            tags=tags,
            notify_source=notify_source,
        )
        payload = SlicePayload(schema_version="1.0", intent=intent, slice=slice_id)
        idem = payload.idempotency_key(row.get("content_sha256"))
        client = Client(self.spool_root, "pm")
        try:
            env = client.send(
                to_peer,
                "request",
                payload.to_dict(),
                correlation_id=slice_id,
                idempotency_key=idem,
                reply_to="pm",
                headers=headers,
            )
        finally:
            client.close()
        return AssignResult(
            slice_id=slice_id,
            row=row,
            correlation_id=slice_id,
            outbound_id=env.id,
        )

    def report(
        self,
        *,
        slice_id: str,
        verdict: str | None = None,
        evidence: str | None = None,
        from_peer: str,
    ) -> dict[str, Any]:
        row = self.store.set_verdict(slice_id, verdict=verdict, evidence=evidence)
        if row is None:
            raise ValueError(f"slice not found: {slice_id}")
        payload = SlicePayload(schema_version="1.0", intent="report", slice=slice_id)
        idem = payload.idempotency_key(row.content_sha256)
        headers = _report_headers(row.notify_source)
        client = Client(self.spool_root, from_peer)
        try:
            env = client.send(
                "pm",
                "event",
                payload.to_dict(),
                correlation_id=slice_id,
                idempotency_key=idem,
                reply_to="pm",
                headers=headers,
            )
        finally:
            client.close()
        return {"slice": slice_id, "row": row.to_dict(), "envelope_id": env.id}

    def _resolve_slice_context(
        self, payload: SlicePayload, *, skip_digest: bool = False
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        row = self.store.get_slice(payload.slice)
        if row is None:
            return None, "slice not registered", "SLICE_NOT_FOUND"
        if not skip_digest:
            try:
                verify_content_digest(row.assignment_path, row.content_sha256)
            except PayloadError as exc:
                return row.to_dict(), str(exc), exc.code
        try:
            read_assignment_body(row.assignment_path)
        except OSError as exc:
            return row.to_dict(), str(exc), "MISSING_ASSIGNMENT"
        return row.to_dict(), None, None

    @staticmethod
    def _fail_ack_payload(
        *,
        code: str,
        message: str,
        slice_id: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "intent": "fail",
            "code": code,
            "message": message,
        }
        if slice_id:
            result["slice"] = slice_id
        return result

    @staticmethod
    def _commit_decision(
        client: Client,
        claimed: ClaimedMessage,
        decision: InboxDecision,
    ) -> None:
        if decision.handled and decision.ack_result is not None:
            client.ack(claimed, result=decision.ack_result)

    def process_claim(
        self,
        claimed: ClaimedMessage,
        *,
        client: Client,
        auto_handle: bool = True,
        auto_handle_intents: frozenset[str] | None = None,
    ) -> InboxDecision:
        if auto_handle_intents is None:
            auto_handle_intents = (
                frozenset({"report_only", "report", "cancel"})
                if auto_handle
                else frozenset({"report_only", "report", "cancel"})
            )
        always_ack_failures = True
        msg_id = claimed.envelope.id
        try:
            payload = parse_payload(claimed.envelope.payload)
        except PayloadError as exc:
            if always_ack_failures:
                ack = self._fail_ack_payload(code=exc.code, message=str(exc))
                return InboxDecision(
                    message_id=msg_id,
                    slice_id="",
                    intent="",
                    disposition="fail",
                    ack_result=ack,
                    error=str(exc),
                    error_code=exc.code,
                    handled=True,
                )
            return InboxDecision(
                message_id=msg_id,
                slice_id="",
                intent="",
                disposition="fail",
                error=str(exc),
                error_code=exc.code,
                handled=False,
            )

        if payload.intent == "assign":
            row = self.store.get_slice(payload.slice)
            if row is not None and row.status in ("RUNNING", "REVIEW") and row.active_job:
                ack = {
                    "intent": "ack",
                    "slice": payload.slice,
                    "status": row.status,
                }
                row_dict = row.to_dict()
                try:
                    body = read_assignment_body(row_dict["assignment_path"])
                except OSError:
                    body = ""
                return InboxDecision(
                    message_id=msg_id,
                    slice_id=payload.slice,
                    intent=payload.intent,
                    disposition="assign_already_running",
                    slice_row=row_dict,
                    assignment_path=row_dict["assignment_path"],
                    assignment_body=body,
                    ack_result=ack,
                    handled=True,
                )

        row_dict, err, code = self._resolve_slice_context(
            payload,
            skip_digest=payload.intent == "report",
        )
        if err:
            if always_ack_failures:
                ack = self._fail_ack_payload(
                    code=code or "ERROR",
                    message=err,
                    slice_id=payload.slice,
                )
                return InboxDecision(
                    message_id=msg_id,
                    slice_id=payload.slice,
                    intent=payload.intent,
                    disposition="fail",
                    slice_row=row_dict,
                    error=err,
                    error_code=code,
                    ack_result=ack,
                    handled=True,
                )
            return InboxDecision(
                message_id=msg_id,
                slice_id=payload.slice,
                intent=payload.intent,
                disposition="fail",
                slice_row=row_dict,
                error=err,
                error_code=code,
                handled=False,
            )

        assert row_dict is not None
        body = read_assignment_body(row_dict["assignment_path"])

        if payload.intent == "cancel":
            if payload.intent in auto_handle_intents:
                ack = {
                    "intent": "ack",
                    "slice": payload.slice,
                    "status": "CANCELLED",
                }
                return InboxDecision(
                    message_id=msg_id,
                    slice_id=payload.slice,
                    intent=payload.intent,
                    disposition="cancelled",
                    slice_row=row_dict,
                    assignment_path=row_dict["assignment_path"],
                    assignment_body=body,
                    ack_result=ack,
                    handled=True,
                )
            return InboxDecision(
                message_id=msg_id,
                slice_id=payload.slice,
                intent=payload.intent,
                disposition="cancel",
                slice_row=row_dict,
                assignment_path=row_dict["assignment_path"],
                assignment_body=body,
                handled=False,
            )

        if payload.intent == "report_only":
            verdict = row_dict.get("verdict") or row_dict.get("status") or "QUEUED"
            if payload.intent in auto_handle_intents:
                ack = {
                    "intent": "ack",
                    "slice": payload.slice,
                    "status": row_dict.get("status", "QUEUED"),
                    "verdict": verdict,
                }
                return InboxDecision(
                    message_id=msg_id,
                    slice_id=payload.slice,
                    intent=payload.intent,
                    disposition="report_only_complete",
                    slice_row=row_dict,
                    assignment_path=row_dict["assignment_path"],
                    assignment_body=body,
                    ack_result=ack,
                    handled=True,
                )
            return InboxDecision(
                message_id=msg_id,
                slice_id=payload.slice,
                intent=payload.intent,
                disposition="report_only",
                slice_row=row_dict,
                assignment_path=row_dict["assignment_path"],
                assignment_body=body,
                handled=False,
            )

        if payload.intent == "report":
            if payload.intent in auto_handle_intents:
                verdict = row_dict.get("verdict") or ""
                evidence = row_dict.get("evidence") or ""
                status = row_dict.get("status") or "QUEUED"
                ack: dict[str, Any] = {
                    "intent": "ack",
                    "slice": payload.slice,
                    "status": status,
                    "verdict": verdict,
                }
                if evidence:
                    ack["evidence"] = evidence
                return InboxDecision(
                    message_id=msg_id,
                    slice_id=payload.slice,
                    intent=payload.intent,
                    disposition="report_received",
                    slice_row=row_dict,
                    assignment_path=row_dict["assignment_path"],
                    ack_result=ack,
                    handled=True,
                )
            return InboxDecision(
                message_id=msg_id,
                slice_id=payload.slice,
                intent=payload.intent,
                disposition="report",
                slice_row=row_dict,
                handled=False,
            )

        if payload.intent == "assign":
            return InboxDecision(
                message_id=msg_id,
                slice_id=payload.slice,
                intent=payload.intent,
                disposition="launch_cursor",
                slice_row=row_dict,
                assignment_path=row_dict["assignment_path"],
                assignment_body=body,
                handled=False,
            )

        return InboxDecision(
            message_id=msg_id,
            slice_id=payload.slice,
            intent=payload.intent,
            disposition="unknown",
            handled=False,
        )

    def process_inbox(
        self,
        peer: str,
        *,
        limit: int = 10,
        auto_handle: bool = True,
        auto_handle_intents: frozenset[str] | None = None,
    ) -> InboxResult:
        client = Client(self.spool_root, peer)
        result = InboxResult(peer=peer)
        try:
            result.reclaimed = client.reclaim_stale()
            processed = 0
            while processed < limit:
                claimed = client.claim()
                if claimed is None:
                    break
                decision = self.process_claim(
                    claimed,
                    client=client,
                    auto_handle=auto_handle,
                    auto_handle_intents=auto_handle_intents,
                )
                self._commit_decision(client, claimed, decision)
                result.decisions.append(decision)
                processed += 1
        finally:
            client.close()
        return result

    def slice_view(self, slice_id: str) -> dict[str, Any]:
        row = self.store.get_slice(slice_id)
        return merge_slice_view(row, slice_id=slice_id, spool_root=self.spool_root)
