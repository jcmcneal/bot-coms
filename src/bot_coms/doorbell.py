"""Reply-stack doorbell: wake ``to`` after spool put (FILO return address).

Control plane is enqueue → doorbell, not pulse cron and not org-chart lookup.
``peers.yaml`` may map peer id → Hermes profile (routing only). Return address
is envelope ``from`` (ack fold uses ``reply_to`` or ``from``).

Origin surface is the return path: Discord-in → Discord-out via
``hermes send --to``; spool peers still get ``hermes chat -Q``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from bot_coms.headers import is_notifiable_source, normalize_source, source_from_headers, source_platform
from bot_coms.profile_env import peer_profile
from bot_coms.types import Envelope

# Platforms that ship via adapter scripts, not Hermes peer chat / Discord send.
_OUT_OF_BAND_PLATFORMS = frozenset({"spm", "webhook", "grok", "grok-spm", "csa", "grok-csa"})

_FALLBACK_PEER_PROFILES: dict[str, str] = {
    "pm": "project-manager",
    "swe": "software-engineer",
    "verifier": "verifier",
    "dna-researcher": "dna-researcher",
    "em": "engineering-manager",
    "ux": "ux-designer",
}

# Injectable for tests.
_wake_runner: Callable[[str, str, Envelope], None] | None = None
_adapter_runner: Callable[[str, Envelope], None] | None = None
_send_runner: Callable[[str, str, Envelope], None] | None = None


def set_wake_runner(runner: Callable[[str, str, Envelope], None] | None) -> None:
    global _wake_runner
    _wake_runner = runner


def set_adapter_runner(runner: Callable[[str, Envelope], None] | None) -> None:
    global _adapter_runner
    _adapter_runner = runner


def set_send_runner(runner: Callable[[str, str, Envelope], None] | None) -> None:
    global _send_runner
    _send_runner = runner


def doorbell_enabled() -> bool:
    raw = os.environ.get("BOT_COMS_DOORBELL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_running_only_ack(
    payload: dict[str, Any] | None,
    *,
    env_type: str = "",
) -> bool:
    """RUNNING ack stamps SQL only — do not wake the return address.

    Also skips ``type=response`` + ``status=RUNNING`` when ``intent`` is missing
    (live bug: extra CLI wake on incomplete RUNNING payloads).
    """
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or "").strip().upper()
    if status != "RUNNING":
        return False
    verdict = str(payload.get("verdict") or "").strip().upper()
    if verdict in {"LANDED", "FAIL"}:
        return False
    intent_raw = payload.get("intent")
    intent = str(intent_raw).strip().lower() if intent_raw is not None else ""
    if intent == "ack":
        return True
    if not intent and (not env_type or env_type == "response"):
        return True
    return False


def is_out_of_band_source(source: str) -> bool:
    platform = source_platform(source)
    return bool(platform) and platform in _OUT_OF_BAND_PLATFORMS


def is_messaging_source(source: str) -> bool:
    """Human messaging surface (discord, telegram, …) — not SPM / cli."""
    return is_notifiable_source(source) and not is_out_of_band_source(source)


def is_terminal_fold(payload: dict[str, Any] | None) -> bool:
    """LANDED / FAIL / report / fail — eligible for out-of-band / gateway delivery."""
    if not isinstance(payload, dict):
        return False
    intent = str(payload.get("intent") or "").strip().lower()
    if intent in {"report", "fail"}:
        return True
    if intent == "ack":
        verdict = str(payload.get("verdict") or "").strip().upper()
        status = str(payload.get("status") or "").strip().upper()
        if verdict in {"LANDED", "FAIL"} or status in {"FAIL", "FAILED"}:
            return True
    return False


def peer_to_hermes_profile(peer_id: str) -> str:
    """Map spool peer id → Hermes ``-p`` profile (routing, not org chart)."""
    peer_id = (peer_id or "").strip()
    if not peer_id:
        return ""
    mapped = peer_profile(peer_id)
    if mapped:
        return mapped
    return _FALLBACK_PEER_PROFILES.get(peer_id, peer_id)


def team_root() -> Path:
    raw = os.environ.get("BOT_COMS_TEAM_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermes" / "team"


def spm_ping_script() -> Path:
    raw = os.environ.get("BOT_COMS_SPM_PING", "").strip()
    if raw:
        return Path(raw).expanduser()
    return team_root() / "ping-spm.sh"


def adapter_ping_script(source: str) -> Path:
    """Return the operator ping script for an out-of-band source prefix."""
    platform = source_platform(source)
    if platform in {"csa", "grok-csa"}:
        raw = os.environ.get("BOT_COMS_CSA_PING", "").strip()
        if raw:
            return Path(raw).expanduser()
        return team_root() / "ping-csa.sh"
    return spm_ping_script()


def hermes_bin() -> str:
    return os.environ.get("HERMES_BIN", "").strip() or str(
        Path.home() / ".local" / "bin" / "hermes"
    )


def _payload_summary(payload: dict[str, Any]) -> str | None:
    from bot_coms.notify import summary_line

    return summary_line(payload)


def build_wake_query(env: Envelope) -> str:
    """Fresh-session wake text for ``hermes chat -Q --query-file`` (cf. pulse wake_assign)."""
    payload = env.payload if isinstance(env.payload, dict) else {}
    intent = str(payload.get("intent") or env.type or "mail").strip()
    slice_id = str(payload.get("slice") or env.correlation_id or "").strip()
    assignment_path = team_root() / "context" / f"{slice_id}.md" if slice_id else None

    if intent == "assign" and slice_id and assignment_path is not None:
        body = ""
        if assignment_path.is_file():
            try:
                body = assignment_path.read_text(encoding="utf-8")[:4000]
            except OSError:
                body = ""
        return (
            f"Slice {slice_id} assigned.\n"
            f"Assignment: {assignment_path}\n\n"
            f"{body}\n\n"
            "Call team_inbox, then launch ONE cursor_screen job for this slice.\n"
            "Stamp team_bus status RUNNING with active_job, then bot_coms_ack. "
            "End turn after launch.\n"
        )

    line = _payload_summary(payload)
    parts = [
        f"bot-coms mail for peer {env.to} (from {env.from_peer}).",
        f"intent={intent}" + (f" slice={slice_id}" if slice_id else "") + ".",
    ]
    if line:
        parts.append(line)
    parts.append(
        "Call team_inbox (or board worker) and handle the envelope. End turn when done."
    )
    return "\n".join(parts) + "\n"


def _default_wake(profile: str, peer_id: str, env: Envelope) -> None:
    """Background ``hermes -p <profile> chat -Q --query-file``; never ``--continue`` / ``-c``."""
    query = build_wake_query(env)
    qf = Path.home() / ".hermes" / "profiles" / profile / f"board-wake-{os.getpid()}.txt"
    qf.parent.mkdir(parents=True, exist_ok=True)
    qf.write_text(query, encoding="utf-8")
    hermes = hermes_bin()
    if profile == "default":
        cmd = [
            hermes,
            "chat",
            "--in",
            "~",
            "-Q",
            "--query-file",
            str(qf),
        ]
    else:
        cmd = [
            hermes,
            "-p",
            profile,
            "chat",
            "--in",
            "~",
            "-Q",
            "--query-file",
            str(qf),
        ]
    subprocess.Popen(  # noqa: S603 — operator-configured hermes path
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _adapter_message(env: Envelope) -> str:
    payload = env.payload if isinstance(env.payload, dict) else {}
    line = _payload_summary(payload)
    if line:
        return line
    intent = str(payload.get("intent") or env.type)
    slice_id = str(payload.get("slice") or env.correlation_id or "")
    return f"bot-coms {intent}" + (f" {slice_id}" if slice_id else "")


def _default_adapter(source: str, env: Envelope) -> None:
    platform = source_platform(source)
    if platform not in _OUT_OF_BAND_PLATFORMS:
        return
    script = adapter_ping_script(source)
    if not script.is_file():
        return
    msg = _adapter_message(env)
    subprocess.Popen(  # noqa: S603 — operator-owned adapter path
        [str(script), msg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _default_gateway_send(profile: str, source: str, env: Envelope) -> None:
    """Relay terminal fold to messaging origin via ``hermes send --to``."""
    source = normalize_source(source)
    if not source:
        return
    hermes = hermes_bin()
    msg = _adapter_message(env)
    if profile == "default":
        cmd = [hermes, "send", "--to", source, msg]
    else:
        cmd = [hermes, "-p", profile, "send", "--to", source, msg]
    subprocess.Popen(  # noqa: S603 — operator-configured hermes path
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def ring(env: Envelope) -> None:
    """Doorbell ``env.to``: messaging origin → gateway send; else Hermes chat -Q.

    Out-of-band ``headers.source`` (e.g. SPM webhook) ships terminal folds via
    the matching adapter — that is how *that* return address delivers, not a
    hardcoded PM→Morgan org edge.
    """
    if not doorbell_enabled():
        return
    payload = env.payload if isinstance(env.payload, dict) else None
    if is_running_only_ack(payload, env_type=env.type or ""):
        return

    source = source_from_headers(env.headers)
    if is_out_of_band_source(source) and is_terminal_fold(payload):
        (_adapter_runner or _default_adapter)(source, env)
        # Peer wake still applies when ``to`` is a spool peer (nested stack).
    elif is_messaging_source(source) and is_terminal_fold(payload):
        peer_id = (env.to or "").strip()
        profile = peer_to_hermes_profile(peer_id) if peer_id else "default"
        if not profile:
            profile = "default"
        (_send_runner or _default_gateway_send)(profile, source, env)
        return

    peer_id = (env.to or "").strip()
    if not peer_id:
        return
    profile = peer_to_hermes_profile(peer_id)
    if not profile:
        return
    (_wake_runner or _default_wake)(profile, peer_id, env)


def after_enqueue(env: Envelope) -> None:
    """Best-effort doorbell after a successful spool put. Never breaks transport."""
    try:
        ring(env)
    except Exception:
        return
