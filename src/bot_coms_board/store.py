"""SQLite coordination ledger at ``~/.hermes/team/bus.sqlite``."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slices (
    id              TEXT PRIMARY KEY,
    to_profile      TEXT NOT NULL,
    from_profile    TEXT NOT NULL,
    peer            TEXT NOT NULL,
    tags            TEXT NOT NULL DEFAULT '[]',
    title           TEXT NOT NULL,
    assignment_path TEXT NOT NULL,
    content_sha256  TEXT,
    notify_source   TEXT,
    status          TEXT NOT NULL,
    verdict         TEXT,
    evidence        TEXT,
    active_job      TEXT,
    superseded_by   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bus_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_slices_status ON slices(status);
CREATE INDEX IF NOT EXISTS idx_slices_to_profile ON slices(to_profile);
CREATE INDEX IF NOT EXISTS idx_bus_log_at ON bus_log(at DESC);
"""

_STATUSES = frozenset(
    {
        "BACKLOG",
        "QUEUED",
        "RUNNING",
        "BLOCKED",
        "REVIEW",
        "DONE",
        "PARKED",
    }
)


def _status_for_verdict(verdict: str) -> tuple[str, bool] | None:
    """Map terminal verdicts to SQL status (and whether to clear active_job)."""
    key = verdict.strip().upper()
    if key == "LANDED":
        return ("REVIEW", True)
    if key == "FAIL":
        return ("BLOCKED", True)
    return None


def default_team_root() -> Path:
    return Path.home() / ".hermes" / "team"


def team_root_from_env() -> Path:
    raw = os.environ.get("BOT_COMS_TEAM_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return default_team_root()


def bus_db_path(*, team_root: Path | None = None) -> Path:
    root = team_root if team_root is not None else team_root_from_env()
    return root / "bus.sqlite"


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SliceRow:
    id: str
    to_profile: str
    from_profile: str
    peer: str
    tags: list[str]
    title: str
    assignment_path: str
    content_sha256: str | None
    notify_source: str | None
    status: str
    verdict: str | None
    evidence: str | None
    active_job: str | None
    superseded_by: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "to_profile": self.to_profile,
            "from_profile": self.from_profile,
            "peer": self.peer,
            "tags": self.tags,
            "title": self.title,
            "assignment_path": self.assignment_path,
            "content_sha256": self.content_sha256,
            "notify_source": self.notify_source,
            "status": self.status,
            "verdict": self.verdict,
            "evidence": self.evidence,
            "active_job": self.active_job,
            "superseded_by": self.superseded_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SliceRow:
        tags_raw = row["tags"] or "[]"
        try:
            tags = json.loads(tags_raw)
        except (TypeError, ValueError):
            tags = []
        if not isinstance(tags, list):
            tags = []
        return cls(
            id=row["id"],
            to_profile=row["to_profile"],
            from_profile=row["from_profile"],
            peer=row["peer"],
            tags=[str(t) for t in tags],
            title=row["title"],
            assignment_path=row["assignment_path"],
            content_sha256=row["content_sha256"],
            notify_source=row["notify_source"] if "notify_source" in row.keys() else None,
            status=row["status"],
            verdict=row["verdict"],
            evidence=row["evidence"],
            active_job=row["active_job"],
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class BusStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
                self._conn.commit()
            else:
                self._migrate(int(row["value"]))

    def _migrate(self, current: int) -> None:
        if current >= _SCHEMA_VERSION:
            return
        if current < 2:
            cols = {
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(slices)").fetchall()
            }
            if "notify_source" not in cols:
                self._conn.execute("ALTER TABLE slices ADD COLUMN notify_source TEXT")
            self._conn.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'version'",
                (str(2),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def register_slice(
        self,
        *,
        slice_id: str,
        to_profile: str,
        from_profile: str,
        peer: str,
        title: str,
        assignment_path: str,
        content_sha256: str | None,
        tags: list[str] | None = None,
        status: str = "QUEUED",
        notify_source: str | None = None,
    ) -> SliceRow:
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status}")
        now = utc_iso()
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM slices WHERE id = ?", (slice_id,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    UPDATE slices SET
                        to_profile = ?, from_profile = ?, peer = ?, tags = ?,
                        title = ?, assignment_path = ?, content_sha256 = ?,
                        notify_source = COALESCE(?, notify_source),
                        status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        to_profile,
                        from_profile,
                        peer,
                        tags_json,
                        title,
                        assignment_path,
                        content_sha256,
                        notify_source,
                        status,
                        now,
                        slice_id,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO slices (
                        id, to_profile, from_profile, peer, tags, title,
                        assignment_path, content_sha256, notify_source, status,
                        verdict, evidence, active_job, superseded_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        slice_id,
                        to_profile,
                        from_profile,
                        peer,
                        tags_json,
                        title,
                        assignment_path,
                        content_sha256,
                        notify_source,
                        status,
                        now,
                        now,
                    ),
                )
            self._conn.commit()
        row = self.get_slice(slice_id)
        assert row is not None
        return row

    def get_slice(self, slice_id: str) -> SliceRow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM slices WHERE id = ?", (slice_id,)
            ).fetchone()
        if row is None:
            return None
        return SliceRow.from_row(row)

    def list_slices(self, *, limit: int = 100) -> list[SliceRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM slices ORDER BY updated_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [SliceRow.from_row(r) for r in rows]

    def set_status(
        self,
        slice_id: str,
        status: str,
        *,
        active_job: str | None = None,
        clear_active_job: bool = False,
    ) -> SliceRow | None:
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status}")
        now = utc_iso()
        with self._lock:
            if clear_active_job:
                self._conn.execute(
                    "UPDATE slices SET status = ?, active_job = NULL, updated_at = ? WHERE id = ?",
                    (status, now, slice_id),
                )
            elif active_job is not None:
                self._conn.execute(
                    "UPDATE slices SET status = ?, active_job = ?, updated_at = ? WHERE id = ?",
                    (status, active_job, now, slice_id),
                )
            else:
                self._conn.execute(
                    "UPDATE slices SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, slice_id),
                )
            self._conn.commit()
        return self.get_slice(slice_id)

    def set_verdict(
        self,
        slice_id: str,
        *,
        verdict: str | None = None,
        evidence: str | None = None,
    ) -> SliceRow | None:
        now = utc_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM slices WHERE id = ?", (slice_id,)
            ).fetchone()
            if row is None:
                return None
            current = SliceRow.from_row(row)
            new_verdict = verdict if verdict is not None else current.verdict
            new_evidence = evidence if evidence is not None else current.evidence
            self._conn.execute(
                """
                UPDATE slices SET verdict = ?, evidence = ?, updated_at = ? WHERE id = ?
                """,
                (new_verdict, new_evidence, now, slice_id),
            )
            self._conn.commit()
        if verdict is not None:
            status_update = _status_for_verdict(verdict)
            if status_update is not None:
                new_status, clear_job = status_update
                return self.set_status(slice_id, new_status, clear_active_job=clear_job)
        return self.get_slice(slice_id)

    def append_log(self, actor: str, message: str, *, at: str | None = None) -> dict[str, Any]:
        stamp = at or utc_iso()
        body = message.strip()
        if "\n" in body or "\r" in body:
            raise ValueError("log message must be a single line")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO bus_log(at, actor, message) VALUES (?, ?, ?)",
                (stamp, actor, body),
            )
            self._conn.commit()
            log_id = cur.lastrowid
        return {"id": log_id, "at": stamp, "actor": actor, "message": body}

    def list_log(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, at, actor, message FROM bus_log ORDER BY at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [
            {"id": r["id"], "at": r["at"], "actor": r["actor"], "message": r["message"]}
            for r in rows
        ]

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._conn.commit()

    def upsert_slice_raw(self, data: dict[str, Any]) -> None:
        """Migration/import helper — insert or replace a full slice row."""
        now = utc_iso()
        tags = data.get("tags") or []
        if isinstance(tags, list):
            tags_json = json.dumps(tags, ensure_ascii=False)
        else:
            tags_json = str(tags)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO slices (
                    id, to_profile, from_profile, peer, tags, title,
                    assignment_path, content_sha256, notify_source, status, verdict, evidence,
                    active_job, superseded_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    to_profile = excluded.to_profile,
                    from_profile = excluded.from_profile,
                    peer = excluded.peer,
                    tags = excluded.tags,
                    title = excluded.title,
                    assignment_path = excluded.assignment_path,
                    content_sha256 = excluded.content_sha256,
                    notify_source = COALESCE(excluded.notify_source, slices.notify_source),
                    status = excluded.status,
                    verdict = excluded.verdict,
                    evidence = excluded.evidence,
                    active_job = excluded.active_job,
                    superseded_by = excluded.superseded_by,
                    updated_at = excluded.updated_at
                """,
                (
                    data["id"],
                    data["to_profile"],
                    data["from_profile"],
                    data["peer"],
                    tags_json,
                    data["title"],
                    data["assignment_path"],
                    data.get("content_sha256"),
                    data.get("notify_source"),
                    data["status"],
                    data.get("verdict"),
                    data.get("evidence"),
                    data.get("active_job"),
                    data.get("superseded_by"),
                    data.get("created_at") or now,
                    data.get("updated_at") or now,
                ),
            )
            self._conn.commit()


def open_store(*, team_root: Path | None = None) -> BusStore:
    return BusStore(bus_db_path(team_root=team_root))


PROFILE_TO_PEER = {
    "software-engineer": "swe",
    "verifier": "verifier",
    "dna-researcher": "dna-researcher",
    "project-manager": "pm",
}

PEER_TO_PROFILE = {v: k for k, v in PROFILE_TO_PEER.items()}


def profile_to_peer(profile: str) -> str:
    return PROFILE_TO_PEER.get(profile, profile)


def peer_to_profile(peer: str) -> str:
    return PEER_TO_PROFILE.get(peer, peer)
