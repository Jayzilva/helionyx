"""SQLite metadata store (SRS §6.1, §6.2).

A thin repository over the standard-library ``sqlite3`` module. Records are
stored as JSON documents keyed by ID, plus the few columns used for lookups.
PostgreSQL (hosted mode, v1.0) will replace this class behind the same methods.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from helionyx.errors import not_found

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites      (id TEXT PRIMARY KEY, doc TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS datasets   (id TEXT PRIMARY KEY, site_id TEXT, kind TEXT, content_hash TEXT,
                                       doc TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scenarios  (id TEXT PRIMARY KEY, root_id TEXT NOT NULL, version INTEGER NOT NULL,
                                       scenario_hash TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0,
                                       doc TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs       (id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, status TEXT NOT NULL,
                                       doc TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (run_id TEXT NOT NULL, candidate_index INTEGER NOT NULL, rank INTEGER,
                                       feasible INTEGER NOT NULL, doc TEXT NOT NULL,
                                       PRIMARY KEY (run_id, candidate_index));
CREATE INDEX IF NOT EXISTS ix_cand_rank ON candidates (run_id, rank);
CREATE TABLE IF NOT EXISTS jobs       (id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                                       owner TEXT NOT NULL, doc TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches    (id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, doc TEXT NOT NULL,
                                       created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artefacts  (id TEXT PRIMARY KEY, run_id TEXT, kind TEXT NOT NULL, uri TEXT NOT NULL,
                                       content_hash TEXT, created_at TEXT NOT NULL);
"""


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ generic documents
    def put(self, table: str, ident: str, doc: dict[str, Any], **cols: Any) -> None:
        names = ["id", "doc", *cols.keys()]
        values = [ident, json.dumps(doc, default=str), *cols.values()]
        if table not in {"candidates"}:
            names.append("created_at")
            values.append(doc.get("created_at") or now_iso())
        sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(names)}) "
               f"VALUES ({', '.join('?' for _ in names)})")
        with self._lock:
            self._conn.execute(sql, values)

    def get(self, table: str, ident: str, kind: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(f"SELECT doc FROM {table} WHERE id = ?", (ident,)).fetchone()
        if row is None:
            raise not_found(kind, ident)
        doc: dict[str, Any] = json.loads(row["doc"])
        return doc

    def exists(self, table: str, ident: str) -> bool:
        with self._lock:
            return self._conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (ident,)).fetchone() is not None

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    # ------------------------------------------------------------------ candidates
    def put_candidates(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM candidates WHERE run_id = ?", (run_id,))
            c.executemany(
                "INSERT INTO candidates (run_id, candidate_index, rank, feasible, doc) VALUES (?, ?, ?, ?, ?)",
                [(run_id, r["candidate_index"], r.get("rank"), int(r["feasible"]), json.dumps(r)) for r in rows],
            )

    def get_candidates(self, run_id: str, *, feasible_only: bool = False, limit: int | None = None,
                       order: str = "rank") -> list[dict[str, Any]]:
        sql = "SELECT doc FROM candidates WHERE run_id = ?"
        if feasible_only:
            sql += " AND feasible = 1"
        sql += " ORDER BY rank IS NULL, rank, candidate_index" if order == "rank" else " ORDER BY candidate_index"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [json.loads(r["doc"]) for r in self.query(sql, (run_id,))]
