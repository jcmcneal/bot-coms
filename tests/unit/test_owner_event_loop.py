"""End-to-end proof of the event-driven accountable-owner supervision loop."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from collections import deque

import pytest

from bot_coms import Client, init_spool
from bot_coms_board.coordinator import TeamCoordinator


@pytest.mark.parametrize('reverse', [False, True])
def test_autonomous_event_pump_three_deep(tmp_path, monkeypatch, reverse):
    """Only queued doorbells schedule actors; duplicates/reordering must quiesce."""
    team = tmp_path / 'team'
    spool = team / 'spool'
    peers = ['owner', 'first', 'second', 'leaf', 'reviewer']
    init_spool(spool, peers)
    monkeypatch.setenv('BOT_COMS_TEAM_ROOT', str(team))
    monkeypatch.setenv('BOT_COMS_DOORBELL', '1')
    (team / 'workflows.json').write_text(json.dumps({
        'version': 1, 'peers': {p: {'profile': p} for p in peers},
        'bindings': {'review': 'reviewer'}, 'defaults': {},
        'policies': {'leaf': {'gates': [{'id': 'quality', 'responsibility': 'review'}]}},
    }))
    queue, accepted = deque(), []
    chain = {'ROOT': ('owner', 'first'), 'MID': ('first', 'second'), 'LEAF': ('second', 'leaf')}
    children = {'ROOT': 'MID', 'MID': 'LEAF'}
    with TeamCoordinator(team_root=team, spool_root=spool) as coord:
        def dispatch(sid, parent=None):
            sender, worker = chain[sid]
            coord.assign(slice_id=sid, from_peer=sender, to_peer=worker,
                         title=sid, assignment_path=_assignment(team, sid),
                         parent_slice=parent, policy='leaf' if sid == 'LEAF' else None)

        def wake(profile, peer, env):
            queue.extend([peer, peer])  # duplicated wake delivery

        with patch('bot_coms.doorbell._wake_runner', wake):
            dispatch('ROOT')
            steps = 0
            while queue:
                steps += 1
                assert steps < 100, 'event loop failed to quiesce'
                peer = queue.pop() if reverse else queue.popleft()
                # Mock agent reacts solely to delivered mail and current ledger.
                client = Client(spool, peer)
                try:
                    for env in client.receive(limit=100):
                        claim = client.claim(env.id)
                        if claim is None:
                            continue
                        d = coord.process_claim(claim, client=client)
                        client.ack(claim, result=d.ack_result)
                        if d.disposition == 'coordinate':
                            sid = d.slice_id
                            if sid in children:
                                dispatch(children[sid], sid)
                            else:
                                coord.report(slice_id=sid, from_peer=peer, verdict='SUBMITTED', evidence='first draft')
                finally:
                    client.close()
                for sid, (owner, worker) in reversed(list(chain.items())):
                    row = coord.store.get_slice(sid)
                    if row is None or row.status == 'DONE':
                        continue
                    revision = row.contract['revision']
                    history = coord.store.workflow_history(sid)
                    reviews = [e for e in history if e['kind'] == 'review' and e['details']['revision'] == revision]
                    if sid == 'LEAF' and peer == 'reviewer' and row.contract['submission'] and not reviews:
                        coord.workflow('review', actor=peer, slice_id=sid, gate='quality', revision=revision,
                                       decision='REJECTED' if revision == 1 else 'APPROVED', evidence='independent check')
                    elif peer == worker and row.status == 'BLOCKED':
                        coord.report(slice_id=sid, from_peer=peer, verdict='SUBMITTED', evidence='corrected draft')
                    elif peer == worker and sid in children and coord.store.get_slice(children[sid]).status == 'DONE' and not row.contract['submission']:
                        coord.report(slice_id=sid, from_peer=peer, verdict='SUBMITTED', evidence='child accepted')
                    elif peer == owner and row.status == 'REVIEW' and row.contract['submission']:
                        if sid == 'LEAF' and (not reviews or reviews[-1]['details']['decision'] != 'APPROVED'):
                            continue
                        coord.workflow('accept', actor=peer, slice_id=sid, revision=revision, evidence='accepted')
                        accepted.append(sid)
            assert accepted == ['LEAF', 'MID', 'ROOT']
            assert all(coord.store.get_slice(sid).status == 'DONE' for sid in chain)
            coord.reconcile()
            assert not queue


def _ack_coordinate_assignment(
    coord: TeamCoordinator,
    spool: Path,
    peer: str,
    slice_id: str,
) -> None:
    client = Client(spool, peer)
    try:
        envelope = next(
            env
            for env in client.receive(limit=50)
            if env.payload.get("intent") == "assign"
            and env.payload.get("slice") == slice_id
        )
        claimed = client.claim(msg_id=envelope.id)
        assert claimed is not None
        decision = coord.process_claim(claimed, client=client, auto_handle=False)
        assert decision.disposition == "coordinate"
        client.ack(
            claimed,
            result={"intent": "ack", "slice": slice_id, "status": "QUEUED"},
        )
    finally:
        client.close()


def _drain_reports(coord: TeamCoordinator, peer: str, *slice_ids: str) -> None:
    result = coord.process_inbox(peer, limit=50)
    reported = {decision.slice_id for decision in result.decisions if decision.intent == "report"}
    assert set(slice_ids) <= reported


def _assignment(team_root: Path, slice_id: str) -> str:
    path = team_root / "context" / f"{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"Coordinate and deliver {slice_id}.\n", encoding="utf-8")
    return str(path)


def test_three_deep_team_bubbles_rework_and_completion_to_each_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doorbells alone drive a rejected leaf back up a three-assignee chain."""
    team_root = tmp_path / "team"
    spool = team_root / "spool"
    peers = [
        "requester",
        "coordinator-one",
        "coordinator-two",
        "builder",
        "root-owner",
        "middle-owner",
        "leaf-owner",
        "leaf-reviewer",
    ]
    init_spool(spool, peers)
    workflows = {
        "version": 1,
        "peers": {
            peer: {"profile": peer, "capabilities": ["coordinate", "review"]}
            for peer in peers
        },
        "bindings": {
            "root_ownership": "root-owner",
            "middle_ownership": "middle-owner",
            "leaf_ownership": "leaf-owner",
            "leaf_review": "leaf-reviewer",
        },
        "defaults": {},
        "policies": {
            "root": {"owner": "root_ownership", "gates": []},
            "middle": {"owner": "middle_ownership", "gates": []},
            "leaf": {
                "owner": "leaf_ownership",
                "gates": [
                    {
                        "id": "quality",
                        "responsibility": "leaf_review",
                        "capability": "review",
                        "independent": True,
                    }
                ],
            },
        },
    }
    team_root.mkdir(exist_ok=True)
    (team_root / "workflows.json").write_text(json.dumps(workflows), encoding="utf-8")
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(spool))
    monkeypatch.setenv("BOT_COMS_DOORBELL", "1")

    wakes: list[tuple[str, str, str]] = []

    def record_wake(_profile: str, peer: str, env) -> None:
        wakes.append(
            (
                peer,
                str(env.payload.get("intent") or ""),
                str(env.payload.get("slice") or ""),
            )
        )

    def take_wakes() -> set[tuple[str, str, str]]:
        current = set(wakes)
        wakes.clear()
        return current

    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    try:
        with patch("bot_coms.doorbell._wake_runner", record_wake):
            # Three deep assignees: coordinator-one -> coordinator-two -> builder.
            coord.assign(
                slice_id="ROOT",
                to_peer="coordinator-one",
                from_peer="requester",
                title="Root delivery",
                assignment_path=_assignment(team_root, "ROOT"),
                activity="coordinate",
                policy="root",
            )
            assert take_wakes() == {("coordinator-one", "assign", "ROOT")}
            _ack_coordinate_assignment(coord, spool, "coordinator-one", "ROOT")

            coord.assign(
                slice_id="MIDDLE",
                to_peer="coordinator-two",
                from_peer="coordinator-one",
                title="Middle delivery",
                assignment_path=_assignment(team_root, "MIDDLE"),
                activity="coordinate",
                policy="middle",
                parent_slice="ROOT",
            )
            assert take_wakes() == {("coordinator-two", "assign", "MIDDLE")}
            _ack_coordinate_assignment(coord, spool, "coordinator-two", "MIDDLE")

            coord.assign(
                slice_id="LEAF",
                to_peer="builder",
                from_peer="coordinator-two",
                title="Leaf delivery",
                assignment_path=_assignment(team_root, "LEAF"),
                activity="coordinate",
                policy="leaf",
                parent_slice="MIDDLE",
            )
            assert take_wakes() == {("builder", "assign", "LEAF")}
            _ack_coordinate_assignment(coord, spool, "builder", "LEAF")

            # A premature parent submission can wake its owner, but acceptance
            # remains fenced until the delegated child chain is accepted.
            coord.report(
                slice_id="ROOT",
                from_peer="coordinator-one",
                verdict="SUBMITTED",
                evidence="premature root submission",
            )
            assert take_wakes() == {
                ("requester", "report", "ROOT"),
                ("root-owner", "report", "ROOT"),
            }
            _drain_reports(coord, "requester", "ROOT")
            _drain_reports(coord, "root-owner", "ROOT")
            with pytest.raises(ValueError, match="child assignment is not accepted"):
                coord.workflow(
                    "accept",
                    actor="root-owner",
                    slice_id="ROOT",
                    revision=1,
                    evidence="cannot skip the middle child",
                )
            assert wakes == []

            # A process completion is not delivery completion. It wakes the worker,
            # return peer, and distinct owner so all three can re-enter their loops.
            coord.store.set_status("LEAF", "RUNNING", active_job="leaf-job")
            coord.report(
                slice_id="LEAF",
                from_peer="builder",
                verdict="EXECUTED",
                evidence="leaf process exit=0; result requires classification",
                job="leaf-job",
            )
            assert take_wakes() == {
                ("builder", "report", "LEAF"),
                ("coordinator-two", "report", "LEAF"),
                ("leaf-owner", "report", "LEAF"),
            }
            _drain_reports(coord, "builder", "LEAF")
            _drain_reports(coord, "leaf-owner", "LEAF")

            # Bottom-level back-and-forth: submit, reject, revise, approve.
            coord.report(
                slice_id="LEAF",
                from_peer="builder",
                verdict="SUBMITTED",
                evidence="revision one artifact",
            )
            assert take_wakes() == {
                ("coordinator-two", "report", "LEAF"),
                ("leaf-owner", "report", "LEAF"),
                ("leaf-reviewer", "report", "LEAF"),
            }
            _drain_reports(coord, "leaf-owner", "LEAF")
            _drain_reports(coord, "leaf-reviewer", "LEAF")
            coord.workflow(
                "review",
                actor="leaf-reviewer",
                slice_id="LEAF",
                gate="quality",
                decision="REJECTED",
                revision=1,
                evidence="missing edge-case evidence",
            )
            assert take_wakes() == {
                ("builder", "report", "LEAF"),
                ("coordinator-two", "report", "LEAF"),
                ("leaf-owner", "report", "LEAF"),
            }
            _drain_reports(coord, "builder", "LEAF")
            _drain_reports(coord, "leaf-owner", "LEAF")

            coord.report(
                slice_id="LEAF",
                from_peer="builder",
                verdict="SUBMITTED",
                evidence="revision two artifact plus edge-case evidence",
            )
            assert take_wakes() == {
                ("coordinator-two", "report", "LEAF"),
                ("leaf-owner", "report", "LEAF"),
                ("leaf-reviewer", "report", "LEAF"),
            }
            _drain_reports(coord, "leaf-owner", "LEAF")
            _drain_reports(coord, "leaf-reviewer", "LEAF")
            with pytest.raises(ValueError, match="current submitted revision"):
                coord.workflow(
                    "review",
                    actor="leaf-reviewer",
                    slice_id="LEAF",
                    gate="quality",
                    decision="APPROVED",
                    revision=1,
                    evidence="stale approval",
                )
            assert wakes == []
            coord.workflow(
                "review",
                actor="leaf-reviewer",
                slice_id="LEAF",
                gate="quality",
                decision="APPROVED",
                revision=2,
                evidence="revision two independently verified",
            )
            assert take_wakes() == {
                ("coordinator-two", "report", "LEAF"),
                ("leaf-owner", "report", "LEAF"),
            }
            _drain_reports(coord, "leaf-owner", "LEAF")
            coord.workflow(
                "accept",
                actor="leaf-owner",
                slice_id="LEAF",
                revision=2,
                evidence="leaf accepted after corrected independent review",
            )
            assert take_wakes() == {("coordinator-two", "report", "LEAF")}

            # The accepted leaf unlocks its parent, which then bubbles to its own
            # owner. A parent cannot jump ahead of an unaccepted child.
            _drain_reports(coord, "coordinator-two", "LEAF")
            coord.report(
                slice_id="MIDDLE",
                from_peer="coordinator-two",
                verdict="SUBMITTED",
                evidence="leaf accepted; middle obligations complete",
            )
            assert take_wakes() == {
                ("coordinator-one", "report", "MIDDLE"),
                ("middle-owner", "report", "MIDDLE"),
            }
            _drain_reports(coord, "middle-owner", "MIDDLE")
            coord.workflow(
                "accept",
                actor="middle-owner",
                slice_id="MIDDLE",
                revision=1,
                evidence="middle accepted with leaf evidence",
            )
            assert take_wakes() == {("coordinator-one", "report", "MIDDLE")}

            _drain_reports(coord, "coordinator-one", "MIDDLE")
            coord.report(
                slice_id="ROOT",
                from_peer="coordinator-one",
                verdict="SUBMITTED",
                evidence="middle accepted; root obligations complete",
            )
            assert take_wakes() == {
                ("requester", "report", "ROOT"),
                ("root-owner", "report", "ROOT"),
            }
            _drain_reports(coord, "root-owner", "ROOT")
            coord.workflow(
                "accept",
                actor="root-owner",
                slice_id="ROOT",
                revision=2,
                evidence="root accepted; no remaining action",
            )

            # The terminal fold reaches the requester. The owner is not woken
            # again by its own final action because the entire tree is now done.
            assert take_wakes() == {("requester", "report", "ROOT")}
            _drain_reports(coord, "requester", "ROOT")
            assert wakes == []
            assert {
                slice_id: coord.store.get_slice(slice_id).status
                for slice_id in ("ROOT", "MIDDLE", "LEAF")
            } == {"ROOT": "DONE", "MIDDLE": "DONE", "LEAF": "DONE"}
    finally:
        coord.store.close()
