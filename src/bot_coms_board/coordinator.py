"""Compose board (bus.sqlite) + bot-coms spool into high-level coordination."""

from __future__ import annotations

import os

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot_coms.session_context import get_env
from bot_coms import Client
from bot_coms.headers import SOURCE_HEADER
from bot_coms.types import ClaimedMessage

from bot_coms_board.payload import (
    PayloadError,
    RECEIPT_INTENTS,
    SlicePayload,
    parse_payload,
    sha256_assignment_spec,
    verify_content_digest,
)
from bot_coms_board.slice_status import incomplete_slice_view, merge_slice_view
from bot_coms_board.workflow import resolve_contract, load_config
from bot_coms_board.store import BusStore, open_store, profile_to_peer, team_root_from_env


def default_spool_root() -> Path:
    raw = get_env("BOT_COMS_SPOOL_ROOT", "").strip()
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


def _dead_sidecar_pid_reason(sidecar: dict[str, Any]) -> str | None:
    pid = sidecar.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "pid_missing"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "pid_dead"
    except PermissionError:
        # The process exists, but belongs to another user.
        return None
    except (OSError, OverflowError):
        return "pid_dead"
    return None


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
    lease_token: str | None = None


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
    reconciliation: dict[str, Any] = field(default_factory=dict)


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
        self.spool_root = spool_root or (self.team_root / "spool" if team_root else default_spool_root())
        self.store = store or open_store(team_root=self.team_root)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.store.close()

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
        contract: dict | None = None,
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
            contract=contract,
        )
        return row.to_dict()

    def assign(
        self,
        *,
        slice_id: str,
        to_peer: str,
        title: str,
        assignment_path: str,
        from_profile: str | None = None,
        from_peer: str | None = None,
        to_profile: str | None = None,
        tags: list[str] | None = None,
        intent: str = "assign",
        headers: dict[str, str] | None = None,
        activity: str = "coordinate",
        policy: str | None = None,
        parent_slice: str | None = None,
    ) -> AssignResult:
        """Enqueue assign to ``to_peer``; return address is envelope ``from`` (FILO)."""
        from bot_coms_board.store import peer_to_profile, profile_to_peer

        if to_profile is None:
            to_profile = peer_to_profile(to_peer)
        sender = (from_peer or get_env("BOT_COMS_PEER_ID") or "").strip()
        if not sender and from_profile:
            sender = profile_to_peer(from_profile)
        if not sender:
            raise ValueError('sender required: BOT_COMS_PEER_ID or from_profile')
        from_profile = peer_to_profile(sender)
        parse_payload({'schema_version': '1.0', 'intent': intent, 'slice': slice_id})
        existing = self.store.get_slice(slice_id)
        if existing and existing.contract:
            contract = existing.contract
            to_profile = existing.to_profile
        else:
            contract = resolve_contract(self.team_root, sender=sender, worker=to_peer,
                                        activity=activity, policy=policy, parent_slice=parent_slice)
            contract['intent'] = intent
        from bot_coms.spool import require_peer
        for peer in {sender, to_peer, contract['owner_peer'], *(g['peer'] for g in contract['gates'])}:
            require_peer(self.spool_root, peer)
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
            contract=contract,
        )
        self.flush_deliveries()
        with self.store._lock:
            env_row = self.store._conn.execute('SELECT message_id FROM workflow_outbox WHERE correlation_id=? ORDER BY event_id LIMIT 1', (slice_id,)).fetchone()
        return AssignResult(slice_id=slice_id, row=row, correlation_id=slice_id,
                            outbound_id=env_row['message_id'] if env_row else '')

    def reconcile(self) -> dict:
        from bot_coms_board.job_done import (
            job_done,
            parse_exit_code,
            read_sidecar,
            resolve_worker_peer,
            tee_path_for,
        )
        completed = []
        # Recover EXIT that preceded the RUNNING stamp, or whose runner hook failed.
        for row in self.store.list_slices(limit=10000):
            if not row.contract or row.status != 'RUNNING' or not row.active_job:
                continue
            stale_job = row.active_job
            try:
                sidecar = read_sidecar(stale_job)
                pid_reason = _dead_sidecar_pid_reason(sidecar)
                sidecar_slice = str(sidecar.get('slice') or '').strip()
                if sidecar_slice != row.id:
                    if pid_reason is None:
                        continue
                    reason = f'slice_mismatch_{pid_reason}'
                    event_details = {
                        'job': stale_job,
                        'reason': reason,
                        'sidecar_slice': sidecar_slice or None,
                    }
                else:
                    exit_code = parse_exit_code(tee_path_for(stale_job, sidecar))
                    job_done_error = None
                    worker_peer = None
                    if exit_code != 'unknown':
                        try:
                            worker_peer = resolve_worker_peer(str(sidecar.get('profile') or ''))
                        except Exception as exc:
                            job_done_error = f'worker identity resolution failed: {exc}'
                        if worker_peer == row.peer:
                            try:
                                result = job_done(
                                    stale_job,
                                    team_root=self.team_root,
                                    spool_root=self.spool_root,
                                )
                                current = self.store.get_slice(row.id)
                                if (
                                    result.get('success')
                                    and current is not None
                                    and (
                                        current.status != 'RUNNING'
                                        or current.active_job != stale_job
                                    )
                                ):
                                    completed.append(result)
                                    continue
                                job_done_error = str(
                                    result.get('error')
                                    or 'job_done did not clear the active assignment'
                                )
                            except Exception as exc:
                                job_done_error = str(exc)
                    if pid_reason is None:
                        continue
                    reason = pid_reason
                    event_details = {'job': stale_job, 'reason': reason}
                    if job_done_error:
                        event_details['job_done_error'] = job_done_error
                    elif exit_code != 'unknown' and worker_peer != row.peer:
                        event_details.update(
                            {
                                'job_done_skipped': 'worker_mismatch',
                                'sidecar_peer': worker_peer or None,
                                'assigned_peer': row.peer,
                            }
                        )
            except FileNotFoundError as exc:
                reason = 'sidecar_missing'
                event_details = {'job': stale_job, 'reason': reason, 'error': str(exc)}
            except (OSError, ValueError) as exc:
                completed.append({'job': stale_job, 'error': str(exc)})
                continue
            if not self.store.recover_dead_job(row.id, stale_job, event_details):
                continue
            completed.append({'job': stale_job, 'slice': row.id, 'recovered': True, 'reason': reason})
        delivery = self.flush_deliveries()
        incomplete = []
        for row in self.store.list_slices(limit=10000):
            fields = incomplete_slice_view(row)
            if fields["open_incomplete"]:
                incomplete.append(
                    {
                        "slice": row.id,
                        "status": row.status,
                        **fields,
                    }
                )
        return {
            'jobs': completed,
            **delivery,
            'open_incomplete': {
                'count': len(incomplete),
                'slices': incomplete,
            },
        }

    def wake_incomplete(self) -> dict[str, Any]:
        """Doorbell assignees and accountable owners for open slices once."""
        from bot_coms.doorbell import ring
        from bot_coms.envelope import new_envelope
        from bot_coms.types import SystemClock

        by_peer: dict[str, dict[str, dict[str, Any]]] = {}
        for row in self.store.list_slices(limit=10000):
            fields = incomplete_slice_view(row)
            if not fields["open_incomplete"]:
                continue
            item = {
                "slice": row.id,
                "status": row.status,
                "incomplete_reason": fields["incomplete_reason"],
            }
            peers = [row.peer]
            owner = str((row.contract or {}).get("owner_peer") or "").strip()
            if owner and owner != row.peer:
                peers.append(owner)
            for peer in peers:
                by_peer.setdefault(peer, {})[row.id] = item

        woken: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        clock = SystemClock()
        for peer, slice_map in by_peer.items():
            slices = list(slice_map.values())
            slice_ids = [item["slice"] for item in slices]
            env = new_envelope(
                from_peer="bot-coms-board",
                to=peer,
                msg_type="event",
                payload={
                    "intent": "open_incomplete",
                    "slice": slice_ids[0],
                    "slices": slice_ids,
                },
                clock=clock,
                ttl_s=300,
            )
            try:
                ring(env, team=self.team_root, spool_root=self.spool_root)
            except Exception as exc:
                errors.append({"peer": peer, "error": str(exc)})
                continue
            woken.append({"peer": peer, "slices": slice_ids})

        return {
            "open_incomplete": {
                "count": len(
                    {
                        item["slice"]
                        for slices in by_peer.values()
                        for item in slices.values()
                    }
                ),
                "slices": list(
                    {
                        item["slice"]: item
                        for slices in by_peer.values()
                        for item in slices.values()
                    }.values()
                ),
            },
            "woken": {
                "count": len(woken),
                "peers": woken,
            },
            "errors": errors,
        }

    def flush_deliveries(self) -> dict:
        import fcntl
        lock_path = self.team_root / 'workflow-relay.lock'
        with lock_path.open('a+b') as lock:
            lock_path.chmod(0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return self._flush_deliveries()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _flush_deliveries(self) -> dict:
        from bot_coms.spool import require_peer, enqueue_inbox
        from bot_coms.envelope import new_envelope
        from bot_coms.types import SystemClock
        from bot_coms.config import SpoolConfig, load_spool_json
        from bot_coms.doorbell import ring
        from bot_coms.atomic import read_json
        from bot_coms.envelope import envelope_from_dict
        delivered, errors = 0, []
        for item in self.store.pending_deliveries(ready_only=True):
            try:
                paths = require_peer(self.spool_root, item['recipient'])
                payload, headers = json.loads(item['payload']), json.loads(item['headers'])
                sender_paths = require_peer(self.spool_root, item['sender'])
                clock = SystemClock()
                config = load_spool_json(sender_paths.spool_json, SpoolConfig())
                from bot_coms.permissions import assert_owner, authorize_send, load_allowlist
                assert_owner(sender_paths.peer_root, allow_foreign_owner=config.allow_foreign_owner)
                assert_owner(paths.peer_root, allow_foreign_owner=config.allow_foreign_owner)
                authorize_send(allowlist=load_allowlist(sender_paths.allowlist), from_peer=item['sender'],
                               to_peer=item['recipient'], token=None)
                env = new_envelope(from_peer=item['sender'], to=item['recipient'],
                    msg_type='response' if headers.get('delivery') == 'external' else 'event',
                    payload=payload, clock=clock, ttl_s=config.default_ttl_s,
                    idempotency_key=item['idem_key'], correlation_id=item['correlation_id'],
                    reply_to=item['sender'], headers=headers, max_payload_bytes=config.max_payload_bytes)
                env.id = item['message_id']
                # Stable ID: replay never creates a second delivery or revives consumed work.
                if headers.get('delivery') == 'external':
                    ring(env, team=self.team_root, spool_root=self.spool_root)
                    self.store.delivery_finished(env.id, final=True)
                    delivered += 1
                    continue
                from bot_coms.lease import reclaim_stale
                reclaim_stale(paths, clock, config)
                if (paths.processing / f"{env.id}.json").exists():
                    self.store.delivery_finished(env.id)
                    continue
                if any((folder / f"{env.id}.json").exists() for folder in (paths.acked, paths.dead_letter)):
                    self.store.delivery_finished(env.id, final=True)
                    continue
                existing = paths.inbox / f"{env.id}.json"
                if existing.exists():
                    env = envelope_from_dict(read_json(existing), max_payload_bytes=config.max_payload_bytes)
                else:
                    enqueue_inbox(paths, env, config=config, clock=clock)
                ring(env, team=self.team_root, spool_root=self.spool_root)  # every assignment gets its own durable, scoped turn
                self.store.delivery_finished(env.id)
                delivered += 1
            except Exception as exc:
                self.store.delivery_finished(item['message_id'], str(exc))
                errors.append({'message_id':item['message_id'], 'error':str(exc)})
        return {'delivered':delivered, 'pending':errors}

    def report(
        self,
        *,
        slice_id: str,
        verdict: str | None = None,
        evidence: str | None = None,
        from_peer: str,
        to_peer: str | None = None,
        job: str | None = None,
    ) -> dict[str, Any]:
        """Report back to whoever assigned (return address), not a hardcoded PM."""
        from bot_coms_board.store import profile_to_peer

        row = self.store.get_slice(slice_id)
        if row is None:
            raise ValueError(f"slice not found: {slice_id}")
        if to_peer and to_peer != (row.contract.get('return_peer') or profile_to_peer(row.from_profile)):
            raise ValueError('report destination must match assignment return address')
        if row.contract:
            self.store.submit_work(slice_id, actor=from_peer, verdict=verdict or 'SUBMITTED', evidence=evidence, job=job)
            delivery = self.flush_deliveries()
            row = self.store.get_slice(slice_id)
            with self.store._lock:
                last = self.store._conn.execute('SELECT message_id FROM workflow_outbox WHERE correlation_id=? ORDER BY event_id DESC LIMIT 1', (slice_id,)).fetchone()
            return {'slice':slice_id, 'row':row.to_dict(), 'to_peer':row.contract['return_peer'],
                    'envelope_id':last['message_id'] if last else '', 'delivery':delivery}
        else:
            if job and row.active_job != job:
                raise ValueError('stale job completion')
            row = self.store.set_verdict(slice_id, verdict=verdict, evidence=evidence)
        # FILO: report to the assigner's peer (from_profile → peer), not org chart.
        return_peer = row.contract.get('return_peer') or profile_to_peer(row.from_profile)
        if to_peer and to_peer != return_peer:
            raise ValueError('report destination must match assignment return address')
        payload = SlicePayload(schema_version="1.0", intent="report", slice=slice_id)
        idem = payload.idempotency_key(row.content_sha256) + ":" + hashlib.sha256(json.dumps([verdict, evidence, row.contract.get("revision")]).encode()).hexdigest()[:16]
        headers = {**(_report_headers(row.notify_source) or {}), "delivery": "internal"}
        client = Client(self.spool_root, from_peer)
        try:
            env = client.send(
                return_peer,
                "event",
                payload.to_dict(),
                correlation_id=slice_id,
                idempotency_key=idem,
                reply_to=from_peer,
                headers=headers,
            )
        finally:
            client.close()
        return {
            "slice": slice_id,
            "row": row.to_dict(),
            "envelope_id": env.id,
            "to_peer": return_peer,
        }

    def _resolve_slice_context(
        self,
        payload: SlicePayload,
        *,
        verify_digest: bool = True,
        require_assignment: bool = True,
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        row = self.store.get_slice(payload.slice)
        if row is None:
            return None, "slice not registered", "SLICE_NOT_FOUND"
        if verify_digest:
            try:
                verify_content_digest(row.assignment_path, row.content_sha256)
            except PayloadError as exc:
                return row.to_dict(), str(exc), exc.code
        if require_assignment:
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
        if decision.handled:
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
            auto_handle_intents = frozenset({"report_only", "report", "cancel"})
        msg_id = claimed.envelope.id
        raw_payload = claimed.envelope.payload
        receipt_intent = raw_payload.get("intent") if isinstance(raw_payload, dict) else None
        if claimed.envelope.type == "response" or receipt_intent in RECEIPT_INTENTS:
            slice_id = raw_payload.get("slice") if isinstance(raw_payload, dict) else None
            return InboxDecision(
                message_id=msg_id,
                slice_id=slice_id if isinstance(slice_id, str) else claimed.envelope.correlation_id,
                intent=receipt_intent if receipt_intent in RECEIPT_INTENTS else "receipt",
                disposition="receipt",
                handled=True,
            )
        try:
            payload = parse_payload(raw_payload)
        except PayloadError as exc:
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
        if payload.intent == "assign":
            row = self.store.get_slice(payload.slice)
            if row and row.contract:
                generation = str(row.contract['generation'])
                stale = (claimed.envelope.to != row.peer or
                         not claimed.envelope.idempotency_key.endswith(':' + generation))
                if stale or row.status in ('DONE', 'CANCELLED', 'PAUSED', 'REVIEW', 'BLOCKED'):
                    return InboxDecision(message_id=msg_id, slice_id=payload.slice, intent='assign',
                                         disposition='assign_inactive', handled=True, ack_result={})
            if row is not None and row.status in ("RUNNING", "REVIEW") and row.active_job:
                ack = {
                    "intent": "ack",
                    "slice": payload.slice,
                    "status": row.status,
                }
                row_dict = row.to_dict()
                try:
                    body = read_assignment_body(row_dict["assignment_path"]) if payload.intent != "report" else ""
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
            verify_digest=payload.intent not in {"report_only", "report"},
            require_assignment=payload.intent != "report",
        )
        if err:
            if payload.intent == 'assign' and row_dict and row_dict.get('contract'):
                self.store.submit_work(payload.slice, actor=claimed.envelope.to,
                                       verdict='ERROR', evidence=f'{code}: {err}')
                self.flush_deliveries()
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
        assert row_dict is not None
        body = (
            read_assignment_body(row_dict["assignment_path"])
            if payload.intent != "report"
            else ""
        )

        if payload.intent == "cancel":
            c = row_dict.get('contract') or {}
            if c and claimed.envelope.from_peer not in {c['owner_peer'], c['return_peer']}:
                return InboxDecision(message_id=msg_id, slice_id=payload.slice, intent='cancel',
                                     disposition='fail', handled=True, ack_result={'intent':'fail', 'code':'NOT_OWNER'})
            if payload.intent in auto_handle_intents:
                # Cancel dispatch, not an OS process. A running job must be paused by its owner first.
                if row_dict.get('active_job'):
                    return InboxDecision(message_id=msg_id, slice_id=payload.slice, intent='cancel',
                                         disposition='fail', handled=True, ack_result={'intent':'fail','code':'ACTIVE_JOB'})
                self.store.set_status(payload.slice, 'CANCELLED')
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
                disposition="coordinate" if row_dict.get("contract", {}).get("activity") == "coordinate" else "launch_agent",
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
        message_id: str | None = None,
    ) -> InboxResult:
        client = Client(self.spool_root, peer)
        result = InboxResult(peer=peer)
        try:
            result.reclaimed = client.reclaim_stale()
            # Recover before listing so replayed messages are visible in this check.
            # An unrelated recovery failure must not block an available inbox.
            try:
                result.reconciliation = self.reconcile()
            except Exception as exc:
                result.reconciliation = {'error': str(exc)}
            processed = 0
            for envelope in client.receive(limit=10000 if message_id else limit):
                if message_id and envelope.id != message_id:
                    continue
                claimed = client.claim(msg_id=envelope.id)
                if claimed is None:
                    continue
                decision = self.process_claim(
                    claimed,
                    client=client,
                    auto_handle=auto_handle,
                    auto_handle_intents=auto_handle_intents,
                )
                decision.lease_token = claimed.lease_token
                self._commit_decision(client, claimed, decision)
                result.decisions.append(decision)
                processed += 1
        finally:
            client.close()
        return result

    def workflow(self, action: str, *, actor: str, slice_id: str, **args) -> dict:
        row = self.store.get_slice(slice_id)
        if row is None:
            raise ValueError('slice not found')
        if action == 'describe':
            return {
                'row': row.to_dict(),
                **incomplete_slice_view(row),
                'history': self.store.workflow_history(slice_id),
            }
        if action == 'review':
            self.store.record_review(slice_id, actor=actor, gate=args['gate'],
                                     decision=args['decision'], evidence=args['evidence'], revision=args['revision'])
            recipients = {row.contract['owner_peer'], row.contract['return_peer']}
            if args['decision'] == 'REJECTED':
                recipients.add(row.peer)
        elif action == 'accept':
            self.store.accept_work(slice_id, actor=actor, evidence=args['evidence'], revision=args['revision'])
            recipients = {row.contract['return_peer']}
        elif action == 'reassign':
            from bot_coms.spool import require_peer
            from bot_coms_board.store import peer_to_profile
            targets = {args['to_peer'], *(args.get('gate_peers') or {}).values()}
            targets.update(v for v in [args.get('owner_peer'), args.get('return_peer')] if v)
            for target in targets:
                require_peer(self.spool_root, target)
            cfg = load_config(self.team_root)
            cap = row.contract.get('worker_capability')
            if cap and cap not in cfg['peers'].get(args['to_peer'], {}).get('capabilities', []):
                raise ValueError('new worker lacks required capability')
            for gate_id, peer in (args.get('gate_peers') or {}).items():
                spec = next((g for g in row.contract['gates'] if g['id'] == gate_id), {})
                capability = spec.get('capability')
                if capability and capability not in cfg['peers'].get(peer, {}).get('capabilities', []):
                    raise ValueError(f'{peer} lacks reviewer capability {capability}')
            row = self.store.reassign_work(slice_id, actor=actor, to_peer=args['to_peer'],
                        to_profile=peer_to_profile(args['to_peer']), reason=args['reason'],
                        owner_peer=args.get('owner_peer'), return_peer=args.get('return_peer'), gate_peers=args.get('gate_peers'))
            recipients = {row.peer}
        else:
            raise ValueError('unknown workflow action')
        row = self.store.get_slice(slice_id)
        delivery = self.flush_deliveries()
        return {'row': row.to_dict(), 'history': self.store.workflow_history(slice_id), 'delivery':delivery}

    def slice_view(self, slice_id: str) -> dict[str, Any]:
        row = self.store.get_slice(slice_id)
        return merge_slice_view(row, slice_id=slice_id, spool_root=self.spool_root)


def reconcile_team(team_root: Path, spool_root: Path | None = None) -> dict:
    """Backend recovery callback, preserving the core/board import boundary."""
    if not (team_root / 'bus.sqlite').exists() and not (team_root / 'workflows.json').exists():
        return {}
    with TeamCoordinator(team_root=team_root, spool_root=spool_root) as coordinator:
        return coordinator.reconcile()
