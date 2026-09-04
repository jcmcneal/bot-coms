"""SQLite idempotency store."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from bot_coms.envelope import format_ts
from bot_coms.types import BeginResult, Clock, DuplicateIdempotency

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency (
    peer TEXT NOT NULL,
    idem_key TEXT NOT NULL,
    status TEXT NOT NULL,
    result_digest TEXT,
    result_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (peer, idem_key)
);
"""


def result_digest(result: dict[str, Any] | None) -> str | None:
    if result is None:
        return None
    raw = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class IdempotencyStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(_SCHEMA)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_idem_gc ON idempotency(status,updated_at)")
        self._conn.commit()
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def gc(self, clock: Clock, retention_s: float) -> None:
        from datetime import timedelta
        cutoff = format_ts(clock.now() - timedelta(seconds=retention_s))
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM idempotency WHERE status='completed' AND updated_at < ?", (cutoff,))

    def get(self, peer: str, key: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM idempotency WHERE peer=? AND idem_key=?",
                (peer, key),
            )
            return cur.fetchone()

    def begin(self, peer: str, key: str, clock: Clock) -> BeginResult:
        now = format_ts(clock.now())
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM idempotency WHERE peer=? AND idem_key=?",
                (peer, key),
            )
            row = cur.fetchone()
            if row is not None:
                if row["status"] == "completed":
                    result = json.loads(row["result_json"]) if row["result_json"] else None
                    return BeginResult("completed", result=result, digest=row["result_digest"])
                if row["status"] == "in_progress":
                    return BeginResult("in_progress")
            try:
                self._conn.execute(
                    "INSERT INTO idempotency (peer, idem_key, status, updated_at) VALUES (?, ?, ?, ?)",
                    (peer, key, "in_progress", now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise DuplicateIdempotency(key) from exc
            return BeginResult("started")

    def complete(self, peer: str, key: str, result: dict[str, Any] | None, clock: Clock) -> None:
        now = format_ts(clock.now())
        digest = result_digest(result)
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result is not None else None
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO idempotency (peer, idem_key, status, result_digest, result_json, updated_at)
                VALUES (?, ?, 'completed', ?, ?, ?)
                ON CONFLICT(peer, idem_key) DO UPDATE SET
                    status='completed',
                    result_digest=excluded.result_digest,
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                (peer, key, digest, payload, now),
            )
            self._conn.commit()

    def abort(self, peer: str, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM idempotency WHERE peer=? AND idem_key=? AND status='in_progress'",
                (peer, key),
            )
            self._conn.commit()
