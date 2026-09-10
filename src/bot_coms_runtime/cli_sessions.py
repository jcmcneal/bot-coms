"""Plugin-owned durable Hermes CLI session execution.

This adapter deliberately uses Hermes's public CLI interface.  It owns the
binding and operation journals, while Hermes owns the session history itself.
It is hosted by a dashboard plugin lifecycle; it is not a launchd worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path


_SESSION_ID = re.compile(r"^session_id:\s*(\S+)\s*$", re.MULTILINE)


class CliSessionRuntime:
    """Small synchronous facade matching the bot-coms execution contract."""

    def __init__(self, root: Path, namespace: str):
        self.root = Path(root)
        self.namespace = namespace
        self.runs = self.root / "plugin-data" / namespace / "runs"
        self.runs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.runs.parent / "cli-sessions.sqlite"
        self._lock = threading.RLock()
        self._processes: dict[tuple[str, str], subprocess.Popen] = {}
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bindings (
                    principal TEXT, profile TEXT, conversation TEXT,
                    session_id TEXT, PRIMARY KEY(principal, profile, conversation));
                CREATE TABLE IF NOT EXISTS operations (
                    principal TEXT, operation_key TEXT, profile TEXT, conversation TEXT,
                    request_hash TEXT, status TEXT, session_id TEXT, result TEXT, error TEXT,
                    active INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
                    PRIMARY KEY(principal, operation_key));
            """)
            db.execute("UPDATE operations SET status='indeterminate', active=0, error=?, updated=? WHERE active=1",
                       ("Dashboard stopped before a terminal receipt", time.time()))
        self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _require(value, name):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a nonempty string")
        return value

    def _receipt(self, row):
        if row is None:
            return None
        result = json.loads(row["result"]) if row["result"] else None
        return dict(status=row["status"], session_id=row["session_id"], result=result,
                    error=row["error"], active=bool(row["active"]),
                    pending_approval=None, not_admitted=False)

    def ensure_session(self, *, principal_id, profile, conversation_key, title=None):
        self._require(principal_id, "principal_id")
        self._require(profile, "profile")
        self._require(conversation_key, "conversation_key")
        with self._lock, self._db() as db:
            db.execute("INSERT OR IGNORE INTO bindings(principal,profile,conversation) VALUES(?,?,?)",
                       (principal_id, profile, conversation_key))
            row = db.execute("SELECT session_id FROM bindings WHERE principal=? AND profile=? AND conversation=?",
                             (principal_id, profile, conversation_key)).fetchone()
        return dict(profile=profile, conversation_key=conversation_key, session_id=row["session_id"])

    def status(self, *, principal_id, operation_key):
        with self._db() as db:
            return self._receipt(db.execute("SELECT * FROM operations WHERE principal=? AND operation_key=?",
                                             (principal_id, operation_key)).fetchone())

    def _hermes(self):
        candidate = Path(sys.executable).with_name("hermes")
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise RuntimeError("The dashboard Python environment does not provide the hermes CLI")

    def submit(self, *, principal_id, profile, conversation_key, operation_key, text,
               title=None, session_env=None, max_turns=None):
        self._require(principal_id, "principal_id")
        self._require(profile, "profile")
        self._require(conversation_key, "conversation_key")
        self._require(operation_key, "operation_key")
        self._require(text, "text")
        env_values = session_env or {}
        if not isinstance(env_values, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                   or k.startswith("HERMES_") for k, v in env_values.items()):
            raise ValueError("session_env must be string values and cannot override HERMES_* identity")
        if max_turns is not None and (type(max_turns) is not int or max_turns < 1):
            raise ValueError("max_turns must be a positive integer")
        request_hash = hashlib.sha256(json.dumps([profile, conversation_key, text, env_values, max_turns],
                                                  sort_keys=True).encode()).hexdigest()
        with self._lock, self._db() as db:
            old = db.execute("SELECT * FROM operations WHERE principal=? AND operation_key=?",
                             (principal_id, operation_key)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ValueError("operation key was already used for another request")
                return self._receipt(old)
            self.ensure_session(principal_id=principal_id, profile=profile, conversation_key=conversation_key)
            binding = db.execute("SELECT session_id FROM bindings WHERE principal=? AND profile=? AND conversation=?",
                                 (principal_id, profile, conversation_key)).fetchone()
            busy = db.execute("SELECT 1 FROM operations WHERE principal=? AND profile=? AND conversation=? AND active=1",
                              (principal_id, profile, conversation_key)).fetchone()
            if busy:
                raise RuntimeError("conversation already has a running turn")
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (principal_id, operation_key, profile, conversation_key, request_hash, "running",
                        binding["session_id"], None, None, 1, time.time()))
        self._start(principal_id, profile, conversation_key, operation_key, text, binding["session_id"],
                    env_values, max_turns)
        return self.status(principal_id=principal_id, operation_key=operation_key)

    def _start(self, principal, profile, conversation, operation, text, session_id, env_values, max_turns):
        directory = self.runs / hashlib.sha256((principal + operation).encode()).hexdigest()
        directory.mkdir(exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False, encoding="utf-8") as query:
            query.write(text)
            query_path = query.name
        os.chmod(query_path, 0o600)
        argv = [self._hermes(), "-p", profile, "chat", "-Q", "--in", "~", "--query-file", query_path,
                "--source", "bot-coms"]
        if session_id:
            argv.extend(["--resume", session_id])
        if max_turns is not None:
            argv.extend(["--max-turns", str(max_turns)])
        env = os.environ.copy()
        env.update(env_values)
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True, env=env)
        key = (principal, operation)
        self._processes[key] = process
        thread = threading.Thread(target=self._wait, args=(key, profile, conversation, query_path, process), daemon=True)
        thread.start()

    def _wait(self, key, profile, conversation, query_path, process):
        principal, operation = key
        try:
            stdout, stderr = process.communicate()
            found = _SESSION_ID.search(stderr or "")
            session_id = found.group(1) if found else None
            status = "completed" if process.returncode == 0 else "failed"
            error = None if status == "completed" else "Hermes CLI turn failed; inspect private dashboard logs"
            with self._lock, self._db() as db:
                current = db.execute("SELECT session_id,status FROM operations WHERE principal=? AND operation_key=?",
                                     key).fetchone()
                if current and current["status"] == "cancelled":
                    return
                sid = session_id or (current["session_id"] if current else None)
                db.execute("UPDATE operations SET status=?,session_id=?,result=?,error=?,active=0,updated=? WHERE principal=? AND operation_key=?",
                           (status, sid, json.dumps({"text": (stdout or "").strip()}) if status == "completed" else None,
                            error, time.time(), principal, operation))
                if sid:
                    db.execute("UPDATE bindings SET session_id=? WHERE principal=? AND profile=? AND conversation=?",
                               (sid, principal, profile, conversation))
        finally:
            self._processes.pop(key, None)
            Path(query_path).unlink(missing_ok=True)
            from bot_coms.dashboard_wake import poke

            poke()

    def cancel(self, *, principal_id, operation_key):
        key = (principal_id, operation_key)
        process = self._processes.get(key)
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        with self._lock, self._db() as db:
            db.execute("UPDATE operations SET status='cancelled',active=0,error=?,updated=? WHERE principal=? AND operation_key=? AND active=1",
                       ("Cancelled by messaging policy", time.time(), principal_id, operation_key))
            return self._receipt(db.execute("SELECT * FROM operations WHERE principal=? AND operation_key=?", key).fetchone())

    def reconcile(self):
        return None

    def forget_conversation(self, *, principal_id, conversation_keys):
        """Drop plugin session bindings and operations for deleted conversations."""
        self._require(principal_id, "principal_id")
        if not isinstance(conversation_keys, (list, tuple)):
            raise ValueError("conversation_keys must be a list")
        keys = [self._require(key, "conversation_key") for key in conversation_keys]
        if not keys:
            return
        with self._lock, self._db() as db:
            for key in keys:
                db.execute(
                    "DELETE FROM operations WHERE principal=? AND conversation=?",
                    (principal_id, key),
                )
                db.execute(
                    "DELETE FROM bindings WHERE principal=? AND conversation=?",
                    (principal_id, key),
                )

    def close(self, cancel=False):
        if cancel:
            for principal, operation in list(self._processes):
                self.cancel(principal_id=principal, operation_key=operation)
