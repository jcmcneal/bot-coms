"""Cursor EXIT → team report (bot-coms owns job-done; no role allowlist).

Reads ``~/.hermes/cursor-screen/<job>.json``, maps Hermes profile → spool peer
via ``peers.yaml``, stamps a mode-aware verdict from the tee ``EXIT:`` line, and enqueues
``team_report`` to the assigner's return address.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_MODES_EXECUTE = frozenset({"write", "force", "execute"})
_SESSION_RE = re.compile(r'"session_id"\s*:\s*"([^"]+)"')


def hermes_home(home: Path | None = None) -> Path:
    return home or Path.home()


def sidecar_path(job: str, *, home: Path | None = None) -> Path:
    return hermes_home(home) / ".hermes" / "cursor-screen" / f"{job}.json"


def tee_path_for(job: str, sidecar: dict[str, Any], *, home: Path | None = None) -> Path:
    raw = str(sidecar.get("out_path") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return hermes_home(home) / ".hermes" / f"{job}.out"


def read_sidecar(job: str, *, home: Path | None = None) -> dict[str, Any]:
    path = sidecar_path(job, home=home)
    if not path.is_file():
        raise FileNotFoundError(f"sidecar missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"sidecar is not an object: {path}")
    return data


def parse_exit_code(tee: Path) -> str:
    """Last ``EXIT:<code>`` line from the Cursor tee, or ``unknown``."""
    if not tee.is_file():
        return "unknown"
    try:
        text = tee.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    code = "unknown"
    for line in text.splitlines():
        if line.startswith("EXIT:"):
            code = line[5:].strip() or "unknown"
    return code


def parse_session_id(tee: Path) -> str:
    if not tee.is_file():
        return ""
    try:
        text = tee.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    matches = _SESSION_RE.findall(text)
    return matches[-1] if matches else ""


def verdict_for_exit(exit_code: str, mode: str = "") -> str:
    """Map Cursor EXIT + launch mode to a board verdict.

    Ask/plan EXIT:0 is a real job-done when that is all the letter asked.
    It is ASK_DONE / PLAN_DONE, not LANDED — LANDED means execute finished.
    """
    mode_norm = (mode or "").strip().lower()
    ok = exit_code == "0" or exit_code in {"", "unknown"}
    if not ok:
        return "FAIL"
    if mode_norm in {"ask", "plan"}:
        return "ASK_DONE" if mode_norm == "ask" else "PLAN_DONE"
    if mode_norm in _MODES_EXECUTE or not mode_norm:
        return "LANDED"
    return "LANDED"


def resolve_worker_peer(profile: str) -> str:
    """Hermes profile → spool peer id via peers.yaml, then static board fallback."""
    from bot_coms.profile_env import profile_to_peer_id
    from bot_coms_board.store import PROFILE_TO_PEER

    profile = (profile or "").strip()
    if not profile:
        return ""
    mapped = profile_to_peer_id(profile)
    if mapped:
        return mapped
    return PROFILE_TO_PEER.get(profile, "")


def _append_log(line: str) -> None:
    log = Path("/tmp/cli-stop-hook.log")
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip() + "\n")
    except OSError:
        pass


_PERSONAL_PROFILE_SOURCE = {
    "cyber-security": "csa:csa",
}


def resolve_job_source(sidecar: dict[str, Any]) -> str:
    """Sidecar source, then personal-profile default. No board required."""
    for key in ("source", "notify"):
        raw = str(sidecar.get(key) or "").strip()
        if raw:
            return raw
    profile = str(sidecar.get("profile") or "").strip()
    return _PERSONAL_PROFILE_SOURCE.get(profile, "")


def _notify_personal(
    *,
    job: str,
    sidecar: dict[str, Any],
    exit_code: str,
    mode: str,
    evidence: str,
) -> dict[str, Any] | None:
    """Wake an out-of-band owner (CSA/SPM) when the job has no board slice."""
    from bot_coms.doorbell import adapter_ping_script, is_out_of_band_source
    from bot_coms.headers import source_platform

    source = resolve_job_source(sidecar)
    if not source:
        return None
    if not is_out_of_band_source(source) and source_platform(source) not in {"csa", "grok-csa"}:
        return None
    script = adapter_ping_script(source)
    if not script.is_file():
        return None
    msg = f"{job} EXIT:{exit_code} mode={mode or '-'} {evidence}"
    try:
        subprocess.Popen(  # noqa: S603 — operator-owned adapter
            [str(script), msg],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        _append_log(f"job-done personal-notify failed job={job}: {exc}")
        return None
    _append_log(f"job-done notify source={source} job={job} profile={sidecar.get('profile')}")
    return {"action": "notify_adapter", "source": source, "script": str(script)}


def job_done(
    job: str,
    *,
    home: Path | None = None,
    mode: str | None = None,
    team_root: Path | None = None,
    spool_root: Path | None = None,
) -> dict[str, Any]:
    """Stamp report for a finished cursor_screen job. Returns a result dict."""
    job = (job or "").strip()
    if not job:
        raise ValueError("job id required")

    sidecar = read_sidecar(job, home=home)
    profile = str(sidecar.get("profile") or "").strip()
    slice_id = str(sidecar.get("slice") or "").strip()
    mode_raw = (mode if mode is not None else str(sidecar.get("mode") or "")).strip()
    mode_norm = mode_raw.lower()

    result: dict[str, Any] = {
        "success": True,
        "job": job,
        "profile": profile,
        "slice": slice_id or None,
        "mode": mode_norm or None,
        "action": "none",
    }

    tee = tee_path_for(job, sidecar, home=home)
    exit_code = parse_exit_code(tee)
    session_id = parse_session_id(tee)
    evidence = f"tee {tee} exit={exit_code} session={session_id or 'none'}"
    result["exit_code"] = exit_code
    result["evidence"] = evidence

    if not slice_id:
        notified = _notify_personal(
            job=job,
            sidecar=sidecar,
            exit_code=exit_code,
            mode=mode_norm,
            evidence=evidence,
        )
        if notified:
            result.update(notified)
            return result
        result["action"] = "skip_no_slice"
        _append_log(f"job-done skip no slice job={job} profile={profile}")
        return result

    peer = resolve_worker_peer(profile)
    if not peer:
        result["success"] = False
        result["action"] = "skip_unmapped_profile"
        result["error"] = f"profile={profile!r} not in peers.yaml"
        _append_log(f"job-done skip unmapped profile={profile} job={job}")
        return result

    from bot_coms_board.coordinator import TeamCoordinator, default_spool_root
    from bot_coms_board.store import team_root_from_env

    verdict = verdict_for_exit(exit_code, mode_norm)
    result["peer"] = peer
    result["verdict"] = verdict

    os.environ.setdefault("BOT_COMS_PEER_ID", peer)
    tr = team_root or team_root_from_env()
    sr = spool_root or default_spool_root()
    coord = TeamCoordinator(team_root=tr, spool_root=sr)
    out = coord.report(
        slice_id=slice_id,
        verdict=verdict,
        evidence=evidence,
        from_peer=peer,
    )
    result["action"] = "report"
    result["report"] = out
    _append_log(
        f"job-done report job={job} profile={profile} peer={peer} "
        f"slice={slice_id} exit={exit_code} verdict={verdict}"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in {"-h", "--help"}:
        sys.stderr.write("usage: bot-coms job-done <job>\n")
        return 2
    job = args[0]
    try:
        out = job_done(job)
    except Exception as exc:
        _append_log(f"job-done error job={job}: {exc}")
        sys.stdout.write(json.dumps({"success": False, "error": str(exc)}, indent=2) + "\n")
        return 1
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
