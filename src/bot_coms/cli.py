"""bot-coms CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from bot_coms.client import Client
from bot_coms.config import env_defaults
from bot_coms.envelope import Envelope
from bot_coms.spool import init_spool
from bot_coms.types import MessageState


def _json_print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _envelope_out(env: Envelope) -> dict[str, Any]:
    return env.to_dict()


def _root(ns: argparse.Namespace) -> Path:
    raw = ns.root or env_defaults()["spool_root"]
    if not raw:
        raise SystemExit("spool root required (--root or BOT_COMS_SPOOL_ROOT)")
    return Path(raw)


def _peer(ns: argparse.Namespace, attr: str = "peer") -> str:
    val = getattr(ns, attr, None) or env_defaults()["peer_id"]
    if not val:
        raise SystemExit("peer id required (--peer/--from or BOT_COMS_PEER_ID)")
    return val


def _token(ns: argparse.Namespace) -> str | None:
    return getattr(ns, "token", None) or env_defaults()["token"]


def _client(ns: argparse.Namespace, peer: str) -> Client:
    return Client(_root(ns), peer, token=_token(ns))


def cmd_init(ns: argparse.Namespace) -> int:
    peers = [p.strip() for p in ns.peers.split(",") if p.strip()]
    path = init_spool(_root(ns), peers)
    _json_print({"root": str(path), "peers": peers})
    return 0


def cmd_send(ns: argparse.Namespace) -> int:
    payload = json.loads(Path(ns.payload_file).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("payload must be a JSON object")
    headers = None
    if ns.headers_file:
        headers = json.loads(Path(ns.headers_file).read_text(encoding="utf-8"))
        if not isinstance(headers, dict):
            raise SystemExit("headers must be a JSON object")
    client = _client(ns, ns.from_peer or env_defaults()["peer_id"] or "")
    env = client.send(
        ns.to,
        ns.type,
        payload,
        idempotency_key=ns.idempotency_key,
        correlation_id=ns.correlation_id,
        reply_to=ns.reply_to,
        ttl_s=ns.ttl,
        headers=headers,
    )
    _json_print(_envelope_out(env))
    return 0


def cmd_claim(ns: argparse.Namespace) -> int:
    client = _client(ns, _peer(ns))
    claimed = client.claim(msg_id=ns.id)
    if claimed is None:
        _json_print({"claimed": False})
        return 0
    _json_print({"claimed": True, "path": str(claimed.path), "envelope": claimed.envelope.to_dict()})
    return 0


def cmd_reclaim(ns: argparse.Namespace) -> int:
    client = _client(ns, _peer(ns))
    reclaimed = client.reclaim_stale()
    _json_print({"peer": client.peer_id, "reclaimed": reclaimed})
    return 0


def cmd_ack(ns: argparse.Namespace) -> int:
    client = _client(ns, _peer(ns))
    claimed = client.claim(msg_id=ns.id)
    if claimed is None:
        # already in processing for this peer — load from processing
        from bot_coms.atomic import read_json
        from bot_coms.envelope import envelope_from_dict
        from bot_coms.types import ClaimedMessage

        path = client.paths.processing / f"{ns.id}.json"
        if not path.is_file():
            raise SystemExit(f"message {ns.id} is not claimed")
        env = envelope_from_dict(read_json(path), max_payload_bytes=client.config.max_payload_bytes)
        claimed = ClaimedMessage(envelope=env, peer_id=client.peer_id, path=path, worker_id=client.worker_id)
    result = None
    if ns.result_file:
        result = json.loads(Path(ns.result_file).read_text(encoding="utf-8"))
    client.ack(claimed, result=result)
    _json_print({"acked": ns.id})
    return 0


def cmd_nack(ns: argparse.Namespace) -> int:
    client = _client(ns, _peer(ns))
    claimed = client.claim(msg_id=ns.id)
    if claimed is None:
        from bot_coms.atomic import read_json
        from bot_coms.envelope import envelope_from_dict
        from bot_coms.types import ClaimedMessage

        path = client.paths.processing / f"{ns.id}.json"
        if not path.is_file():
            raise SystemExit(f"message {ns.id} is not claimed")
        env = envelope_from_dict(read_json(path), max_payload_bytes=client.config.max_payload_bytes)
        claimed = ClaimedMessage(envelope=env, peer_id=client.peer_id, path=path, worker_id=client.worker_id)
    client.nack(claimed, error=ns.error, retryable=not ns.no_retry)
    _json_print({"nacked": ns.id})
    return 0


def cmd_status(ns: argparse.Namespace) -> int:
    client = _client(ns, _peer(ns))
    if ns.id:
        st: MessageState = client.status(ns.id)
        payload: dict[str, Any] = {
            "id": st.id,
            "location": st.location,
            "dead_letter_reason": st.dead_letter_reason,
        }
        if st.envelope:
            payload["envelope"] = st.envelope.to_dict()
        _json_print(payload)
        return 0
    counts = {
        name: len(list(getattr(client.paths, name).glob("*.json")))
        if name != "dead_letter"
        else len(list(client.paths.dead_letter.glob("*.json")))
        for name in ("inbox", "outbox", "processing", "acked", "results", "dead_letter")
    }
    _json_print({"peer": client.peer_id, "counts": counts})
    return 0


def cmd_worker(ns: argparse.Namespace) -> int:
    import importlib
    import threading

    from bot_coms.worker import Worker

    if ns.notify_argv:
        os.environ["BOT_COMS_NOTIFY_ARGV"] = json.dumps(ns.notify_argv)
    spec = ns.handler
    mod_name, func_name = spec.rsplit(":", 1)
    func = getattr(importlib.import_module(mod_name), func_name)
    client = _client(ns, _peer(ns))
    worker = Worker(client, func)
    if ns.idle_rounds is not None:
        worker.run_until_idle(idle_rounds=ns.idle_rounds)
        return 0
    stop = threading.Event()
    worker.run_forever(stop=stop)
    return 0


def cmd_smoke(ns: argparse.Namespace) -> int:
    import pytest

    args = ["tests/smoke", "-q"]
    if not ns.all:
        args = ["tests/smoke", "-q"]
    return pytest.main(args)


def cmd_job_done(ns: argparse.Namespace) -> int:
    """Cursor EXIT callback: sidecar + peers.yaml → team_report (no allowlist).

    Loads ``bot_coms_board.job_done`` via importlib so the transport package
    never statically imports the board (import-boundary test).
    """
    import importlib

    try:
        mod = importlib.import_module("bot_coms_board.job_done")
        out = mod.job_done(ns.job)
    except Exception as exc:
        _json_print({"success": False, "error": str(exc)})
        return 1
    _json_print(out)
    return 0 if out.get("success") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bot-coms")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init-spool")
    init.add_argument("--root", default=None)
    init.add_argument("--peers", required=True)
    init.set_defaults(func=cmd_init)

    send = sub.add_parser("send")
    send.add_argument("--root", default=None)
    send.add_argument("--from", dest="from_peer", default=None)
    send.add_argument("--to", required=True)
    send.add_argument("--type", required=True)
    send.add_argument("--payload-file", required=True)
    send.add_argument("--idempotency-key", default=None)
    send.add_argument("--correlation-id", default=None)
    send.add_argument("--reply-to", default=None)
    send.add_argument("--headers-file", default=None)
    send.add_argument("--ttl", type=float, default=None)
    send.add_argument("--token", default=None)
    send.set_defaults(func=cmd_send)

    claim = sub.add_parser("claim")
    claim.add_argument("--root", default=None)
    claim.add_argument("--peer", default=None)
    claim.add_argument("--id", default=None)
    claim.add_argument("--token", default=None)
    claim.set_defaults(func=cmd_claim)

    reclaim = sub.add_parser("reclaim")
    reclaim.add_argument("--root", default=None)
    reclaim.add_argument("--peer", default=None)
    reclaim.add_argument("--token", default=None)
    reclaim.set_defaults(func=cmd_reclaim)

    ack = sub.add_parser("ack")
    ack.add_argument("--root", default=None)
    ack.add_argument("--peer", default=None)
    ack.add_argument("--id", required=True)
    ack.add_argument("--result-file", default=None)
    ack.add_argument("--token", default=None)
    ack.set_defaults(func=cmd_ack)

    nack = sub.add_parser("nack")
    nack.add_argument("--root", default=None)
    nack.add_argument("--peer", default=None)
    nack.add_argument("--id", required=True)
    nack.add_argument("--error", required=True)
    nack.add_argument("--no-retry", action="store_true")
    nack.add_argument("--token", default=None)
    nack.set_defaults(func=cmd_nack)

    status = sub.add_parser("status")
    status.add_argument("--root", default=None)
    status.add_argument("--peer", default=None)
    status.add_argument("--id", default=None)
    status.add_argument("--token", default=None)
    status.set_defaults(func=cmd_status)

    worker = sub.add_parser("worker")
    worker.add_argument("--root", default=None)
    worker.add_argument("--peer", default=None)
    worker.add_argument("--handler", required=True)
    worker.add_argument(
        "--notify-argv",
        action="append",
        default=None,
        help="Notify argv fragment; repeat for each token. Use {source} for headers.source.",
    )
    worker.add_argument(
        "--idle-rounds",
        type=int,
        default=None,
        help="Exit after this many empty poll rounds (pulse/cron mode).",
    )
    worker.add_argument("--token", default=None)
    worker.set_defaults(func=cmd_worker)

    smoke = sub.add_parser("smoke")
    smoke.add_argument("--all", action="store_true")
    smoke.set_defaults(func=cmd_smoke)

    job_done_p = sub.add_parser(
        "job-done",
        help="Cursor EXIT → report via peers.yaml (sidecar ~/.hermes/cursor-screen/<job>.json)",
    )
    job_done_p.add_argument("job", help="cursor_screen job id")
    job_done_p.set_defaults(func=cmd_job_done)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        return int(ns.func(ns))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
