"""Reply-stack doorbell: FILO return address, no org chart."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot_coms import Client
from bot_coms.doorbell import (
    is_running_only_ack,
    peer_to_hermes_profile,
    set_adapter_runner,
    set_send_runner,
    set_wake_runner,
)
from bot_coms.spool import init_spool
from bot_coms_board.coordinator import TeamCoordinator


@pytest.fixture
def wakes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOT_COMS_DOORBELL", "1")
    recorded: list[tuple[str, str]] = []
    adapters: list[str] = []
    sends: list[tuple[str, str]] = []

    def wake(profile: str, peer_id: str, env) -> None:
        recorded.append((peer_id, profile))

    def adapter(source: str, env) -> None:
        adapters.append(source)

    def send(profile: str, source: str, env) -> None:
        sends.append((profile, source))

    set_wake_runner(wake)
    set_adapter_runner(adapter)
    set_send_runner(send)
    yield recorded, adapters, sends
    set_wake_runner(None)
    set_adapter_runner(None)
    set_send_runner(None)


@pytest.fixture
def stack_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    team_root = tmp_path / ".hermes" / "team"
    spool = team_root / "spool"
    init_spool(spool, ["pm", "swe", "em", "a", "b"])
    peers = team_root / "peers.yaml"
    peers.write_text(
        "peers:\n"
        "  - id: pm\n    profile: project-manager\n"
        "  - id: swe\n    profile: software-engineer\n"
        "  - id: em\n    profile: engineering-manager\n"
        "  - id: a\n    profile: peer-a\n"
        "  - id: b\n    profile: peer-b\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    monkeypatch.setenv("BOT_COMS_SPOOL_ROOT", str(spool))
    monkeypatch.setenv("BOT_COMS_PEERS_YAML", str(peers))
    return team_root, spool


def test_peer_profile_map_is_routing_not_hardcoded_swe():
    # Map may resolve swe → software-engineer; notify must not hardcode that string
    # as the wake target peer — wake target is the envelope ``to`` peer id.
    assert peer_to_hermes_profile("swe") in {"software-engineer", "swe"}
    assert peer_to_hermes_profile("em") in {"engineering-manager", "em"}


def test_running_only_ack_predicate() -> None:
    assert is_running_only_ack({"intent": "ack", "slice": "S1", "status": "RUNNING"})
    assert is_running_only_ack(
        {"slice": "S1", "status": "RUNNING"},
        env_type="response",
    )
    assert not is_running_only_ack(
        {"intent": "ack", "slice": "S1", "status": "REVIEW", "verdict": "LANDED"}
    )
    assert not is_running_only_ack({"intent": "assign", "slice": "S1"})
    assert not is_running_only_ack({"intent": "fail", "code": "X"})


def test_assign_a_to_b_doorbells_b_not_hardcoded_swe(stack_env, wakes, monkeypatch):
    team_root, spool = stack_env
    recorded, _adapters, _sends = wakes
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    ctx = team_root / "context" / "S100.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("work\n", encoding="utf-8")

    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S100",
        to_peer="b",
        title="nest",
        assignment_path=str(ctx),
        from_peer="a",
        from_profile="peer-a",
        to_profile="peer-b",
    )

    assert recorded == [("b", "peer-b")]
    # Must not wake a hardcoded software-engineer profile as the notify target.
    assert all(peer != "software-engineer" for peer, _prof in recorded)
    assert all(prof != "software-engineer" or peer == "swe" for peer, prof in recorded)


def test_report_b_to_a_doorbells_a_even_if_a_is_not_pm(stack_env, wakes, monkeypatch):
    team_root, spool = stack_env
    recorded, _adapters, _sends = wakes
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    ctx = team_root / "context" / "S101.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("work\n", encoding="utf-8")

    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S101",
        to_peer="b",
        title="nest",
        assignment_path=str(ctx),
        from_peer="a",
        from_profile="peer-a",
        to_profile="peer-b",
    )
    recorded.clear()

    out = coord.report(
        slice_id="S101",
        verdict="LANDED",
        evidence="ok",
        from_peer="b",
    )
    assert out["to_peer"] == "a"
    assert recorded == [("a", "peer-a")]
    inbox = list((spool / "a" / "inbox").glob("*.json"))
    assert inbox, "report must land in original from peer inbox"


def test_running_ack_does_not_wake(stack_env, wakes, monkeypatch):
    team_root, spool = stack_env
    recorded, _adapters, _sends = wakes
    monkeypatch.setenv("BOT_COMS_PEER_ID", "a")

    ctx = team_root / "context" / "S102.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("work\n", encoding="utf-8")

    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    result = coord.assign(
        slice_id="S102",
        to_peer="b",
        title="run",
        assignment_path=str(ctx),
        from_peer="a",
        from_profile="peer-a",
        to_profile="peer-b",
    )
    recorded.clear()

    b = Client(spool, "b")
    claimed = b.claim(msg_id=result.outbound_id)
    assert claimed is not None
    coord.store.set_status("S102", "RUNNING", active_job="job-1")
    b.ack(
        claimed,
        result={"intent": "ack", "slice": "S102", "status": "RUNNING"},
    )
    b.close()

    assert recorded == [], "RUNNING-only ack must not doorbell return address"
    # Response may still be in A's inbox for audit — just no wake.
    assert list((spool / "a" / "inbox").glob("*.json")) or list(
        (spool / "a" / "results").glob("*.json")
    )


def test_pulse_not_required_to_drain_inbox(stack_env, wakes, monkeypatch):
    """Doorbell on enqueue is enough; no pulse sweep needed to wake ``to``."""
    team_root, spool = stack_env
    recorded, _adapters, _sends = wakes
    monkeypatch.setenv("BOT_COMS_PEER_ID", "em")

    ctx = team_root / "context" / "S103.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("work\n", encoding="utf-8")

    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S103",
        to_peer="swe",
        title="pulse-free",
        assignment_path=str(ctx),
        from_peer="em",
        from_profile="engineering-manager",
    )
    assert recorded == [("swe", "software-engineer")]
    assert list((spool / "swe" / "inbox").glob("*.json"))


def test_fail_ack_doorbells_return_address(stack_env, wakes, monkeypatch):
    _team_root, spool = stack_env
    recorded, _adapters, _sends = wakes
    monkeypatch.setenv("BOT_COMS_DOORBELL", "1")

    a = Client(spool, "a")
    env = a.send(
        "b",
        "request",
        {"intent": "assign", "slice": "SX"},
        reply_to="a",
    )
    recorded.clear()
    b = Client(spool, "b")
    claimed = b.claim(msg_id=env.id)
    assert claimed is not None
    b.ack(
        claimed,
        result={"intent": "fail", "slice": "SX", "code": "BOOM", "message": "nope"},
    )
    b.close()
    a.close()
    assert recorded == []  # failure receipts are recorded without another model wake


def test_spm_out_of_band_uses_adapter(stack_env, wakes, monkeypatch):
    team_root, spool = stack_env
    recorded, adapters, sends = wakes
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")

    ctx = team_root / "context" / "S104.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("work\n", encoding="utf-8")

    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S104",
        to_peer="swe",
        title="spm",
        assignment_path=str(ctx),
        from_peer="pm",
        headers={"source": "spm:webhook"},
    )
    recorded.clear()
    adapters.clear()
    sends.clear()
    coord.report(slice_id="S104", verdict="LANDED", evidence="done", from_peer="swe")
    assert ("pm", "project-manager") in recorded
    assert adapters == []
    coord.workflow("accept", actor="pm", slice_id="S104", revision=1, evidence="Accepted with evidence")
    assert adapters == ["spm:webhook"]
    assert sends == []


def test_discord_terminal_fold_uses_gateway_send_not_chat(stack_env, wakes, monkeypatch):
    team_root, spool = stack_env
    recorded, adapters, sends = wakes
    monkeypatch.setenv("BOT_COMS_PEER_ID", "pm")

    ctx = team_root / "context" / "S105.md"
    ctx.parent.mkdir(parents=True)
    ctx.write_text("work\n", encoding="utf-8")

    coord = TeamCoordinator(team_root=team_root, spool_root=spool)
    coord.assign(
        slice_id="S105",
        to_peer="swe",
        title="discord-origin",
        assignment_path=str(ctx),
        from_peer="pm",
        headers={"source": "discord:chan:thread"},
    )
    recorded.clear()
    adapters.clear()
    sends.clear()
    coord.report(slice_id="S105", verdict="LANDED", evidence="done", from_peer="swe")
    assert recorded == [("pm", "project-manager")], "Internal report must reach reviewer"
    assert sends == []
    coord.workflow("accept", actor="pm", slice_id="S105", revision=1, evidence="Accepted with evidence")
    assert adapters == []
    assert sends == [("project-manager", "discord:chan:thread")]


def test_running_response_without_intent_does_not_wake(stack_env, wakes, monkeypatch):
    _team_root, spool = stack_env
    recorded, _adapters, sends = wakes
    monkeypatch.setenv("BOT_COMS_DOORBELL", "1")

    a = Client(spool, "a")
    env = a.send(
        "b",
        "request",
        {"intent": "assign", "slice": "SR"},
        reply_to="a",
    )
    recorded.clear()
    sends.clear()
    b = Client(spool, "b")
    claimed = b.claim(msg_id=env.id)
    assert claimed is not None
    b.ack(claimed, result={"slice": "SR", "status": "RUNNING"})
    b.close()
    a.close()
    assert recorded == []
    assert sends == []
