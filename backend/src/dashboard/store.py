"""
SQLite persistence layer for trace records.

Write-through on every ``TraceBuffer.append()``; restore on startup.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path("logs/traces.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    method          TEXT NOT NULL DEFAULT 'POST',
    path            TEXT NOT NULL DEFAULT '',
    source_format   TEXT NOT NULL DEFAULT '',
    target_model    TEXT NOT NULL DEFAULT '',
    stream          INTEGER NOT NULL DEFAULT 0,
    status_code     INTEGER NOT NULL DEFAULT 200,
    total_duration_ms REAL NOT NULL DEFAULT 0.0,
    original_body   TEXT NOT NULL DEFAULT '{}',
    stages          TEXT NOT NULL DEFAULT '[]',
    replay_of       TEXT,
    replays         TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_traces_timestamp ON traces(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_traces_replay_of ON traces(replay_of);
"""


class TraceStore:
    """SQLite-backed store for TraceRecord persistence.

    Usage::

        store = TraceStore("logs/traces.db")
        store.save(record)       # write-through on append
        records = store.load(500)  # restore on startup
    """

    def __init__(self, db_path: str = "") -> None:
        path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.executescript(_SCHEMA)
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, record: dict) -> None:
        """Insert or replace a serialized trace record."""
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.execute(
                """INSERT OR REPLACE INTO traces
                   (id, timestamp, method, path, source_format, target_model,
                    stream, status_code, total_duration_ms,
                    original_body, stages, replay_of, replays)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["timestamp"],
                    record.get("method", "POST"),
                    record.get("path", ""),
                    record.get("source_format", ""),
                    record.get("target_model", ""),
                    1 if record.get("stream") else 0,
                    record.get("status_code", 200),
                    record.get("total_duration_ms", 0.0),
                    json.dumps(record.get("original_body", {}), ensure_ascii=False),
                    json.dumps(record.get("stages", []), ensure_ascii=False, default=str),
                    record.get("replay_of"),
                    json.dumps(record.get("replays", []), ensure_ascii=False),
                ),
            )
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self, limit: int = 1000) -> list[dict]:
        """Load the most recent *limit* trace records, newest first."""
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM traces ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()

        records: list[dict] = []
        for row in rows:
            r = dict(row)
            r["stream"] = bool(r["stream"])
            r["original_body"] = _json_load(r.get("original_body", "{}"))
            r["stages"] = _json_load(r.get("stages", "[]"))
            r["replays"] = _json_load(r.get("replays", "[]"))
            records.append(r)

        return records

    def count(self) -> int:
        """Return total row count."""
        with self._lock:
            conn = sqlite3.connect(self._path)
            row = conn.execute("SELECT COUNT(*) FROM traces").fetchone()
            conn.close()
        return row[0] if row else 0

    def vacuum(self) -> None:
        """Reclaim disk space after bulk deletes."""
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.execute("VACUUM")
            conn.close()


def _json_load(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw if isinstance(raw, (list, dict)) else []
