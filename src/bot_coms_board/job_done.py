"""Cursor EXIT → team report (bot-coms owns job-done; no role allowlist).

Reads ``~/.hermes/cursor-screen/<job>.json``, maps Hermes profile → spool peer
via ``peers.yaml``, stamps LANDED/FAIL from the tee ``EXIT:`` line, and enqueues
``team_report`` to the assigner's return address.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_MODES_SKIP = frozenset({"write"})
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


def verdict_for_exit(exit_code: str) -> str:
    """EXIT:0 (or unknown) → LANDED; any other numeric → FAIL."""
    if exit_code == "0" or exit_code in {"", "unknown"}:
        return "LANDED"
    return "FAIL"


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

    if mode_norm in _MODES_SKIP:
        result["action"] = "skip_write"
        _append_log(f"job-done skip mode=write job={job} profile={profile}")
        return result

    tee = tee_path_for(job, sidecar, home=home)
    exit_code = parse_exit_code(tee)
    session_id = parse_session_id(tee)
    evidence = f"tee {tee} exit={exit_code} session={session_id or 'none'}"
    result["exit_code"] = exit_code
    result["evidence"] = evidence

    if not slice_id:
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

    verdict = verdict_for_exit(exit_code)
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
