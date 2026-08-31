"""Peer-root layout helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PEER_SUBDIRS = (
    "inbox",
    "outbox",
    "processing",
    "acked",
    "results",
    "dead-letter",
    "tmp",
    "state",
    "state/leases",
)


@dataclass(frozen=True)
class PeerPaths:
    root: Path
    peer_id: str
    peer_root: Path
    inbox: Path
    outbox: Path
    processing: Path
    acked: Path
    results: Path
    dead_letter: Path
    tmp: Path
    state: Path
    leases: Path
    spool_json: Path
    allowlist: Path
    idempotency_db: Path
    audit: Path

    def message_path(self, folder: str, msg_id: str) -> Path:
        mapping = {
            "inbox": self.inbox,
            "outbox": self.outbox,
            "processing": self.processing,
            "acked": self.acked,
            "results": self.results,
            "dead-letter": self.dead_letter,
        }
        return mapping[folder] / f"{msg_id}.json"

    def lease_path(self, msg_id: str) -> Path:
        return self.leases / f"{msg_id}.json"

    def result_path(self, correlation_id: str) -> Path:
        return self.results / f"{correlation_id}.json"


def peer_paths(spool_root: Path, peer_id: str) -> PeerPaths:
    peer_root = spool_root / peer_id
    state = peer_root / "state"
    return PeerPaths(
        root=spool_root,
        peer_id=peer_id,
        peer_root=peer_root,
        inbox=peer_root / "inbox",
        outbox=peer_root / "outbox",
        processing=peer_root / "processing",
        acked=peer_root / "acked",
        results=peer_root / "results",
        dead_letter=peer_root / "dead-letter",
        tmp=peer_root / "tmp",
        state=state,
        leases=state / "leases",
        spool_json=state / "spool.json",
        allowlist=state / "allowlist.json",
        idempotency_db=state / "idempotency.sqlite",
        audit=state / "audit.jsonl",
    )
