"""The site's own operational state in SQLite (WAL): the files of the record read in, the update history, accounts
and sessions, question and visit counts, feedback, topic versions, featured briefings and the site's own keys.

SQLite rather than DuckDB because the web app and the scheduled sync are
separate processes that both write here; DuckDB permits one writing process.
Analytical queries use DuckDB over the Parquet store instead.

The same file holds the Where They Stand sites' shared package's tables (``wts_…``: spending, the answer cache,
saved briefings, notes of failures, reports, statements, the record of each briefing), which hansard/store/wts.py
opens. Before the shared package the site kept those in tables of its own here (``usage``, ``answer_cache``,
``briefings``, ``failures``, ``reports``, ``statements``): a database from then still has them, their rows are
copied across once, and this module no longer makes or reads them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS manifest (
    source      TEXT NOT NULL,
    key         TEXT NOT NULL,
    revision    TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    parsed_at   TEXT NOT NULL,
    indexed_at  TEXT,
    embedded_at TEXT,
    PRIMARY KEY (source, key)
);
CREATE TABLE IF NOT EXISTS sync_log (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    stats       TEXT,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
-- how often each question is asked, per day (the text and a count only; nothing about who asked)
CREATE TABLE IF NOT EXISTS question_counts (
    day       TEXT NOT NULL,
    question  TEXT NOT NULL,
    n         INTEGER NOT NULL,
    PRIMARY KEY (day, question)
);
-- topic briefings: each version written for a standing question (config/topics.toml); the newest is current
CREATE TABLE IF NOT EXISTS topic_versions (
    slug          TEXT NOT NULL,
    briefing_id   TEXT NOT NULL,
    prepared_at   TEXT NOT NULL,
    records_up_to TEXT,
    PRIMARY KEY (slug, briefing_id)
);
-- when each topic was last checked, and why it was or was not rewritten
CREATE TABLE IF NOT EXISTS topic_checks (
    slug        TEXT PRIMARY KEY,
    checked_at  TEXT NOT NULL,
    outcome     TEXT NOT NULL
);
-- briefings chosen by the editor to be listed on /topics and found by search engines
CREATE TABLE IF NOT EXISTS featured (
    briefing_id  TEXT PRIMARY KEY,
    title        TEXT,
    blurb        TEXT,
    featured_at  TEXT NOT NULL
);
-- "Was this briefing useful?" answers
CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    briefing_key TEXT,
    question     TEXT NOT NULL,
    useful       INTEGER NOT NULL,
    visitor      TEXT
);
-- cookie-free visit counts: totals per day only. Visitor hashes are kept for the current day
-- (to count unique visitors) and rolled up into day_totals, then deleted.
CREATE TABLE IF NOT EXISTS page_views (
    day   TEXT NOT NULL,
    path  TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, path)
);
CREATE TABLE IF NOT EXISTS referrers (
    day   TEXT NOT NULL,
    host  TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, host)
);
CREATE TABLE IF NOT EXISTS day_visitors (
    day     TEXT NOT NULL,
    visitor TEXT NOT NULL,
    PRIMARY KEY (day, visitor)
);
CREATE TABLE IF NOT EXISTS day_totals (
    day      TEXT PRIMARY KEY,
    visitors INTEGER NOT NULL
);
-- how many of a day's visitors did each thing that shows the site working (asked a question, opened a source,
-- asked a follow-up): today's visitor hashes, rolled up into event_totals and deleted, as for day_visitors
CREATE TABLE IF NOT EXISTS day_events (
    day     TEXT NOT NULL,
    event   TEXT NOT NULL,
    visitor TEXT NOT NULL,
    PRIMARY KEY (day, event, visitor)
);
CREATE TABLE IF NOT EXISTS event_totals (
    day      TEXT NOT NULL,
    event    TEXT NOT NULL,
    visitors INTEGER NOT NULL,
    PRIMARY KEY (day, event)
);
"""

def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StateDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as con:
            con.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            self._local.con = con
        return con

    @contextmanager
    def connect(self):
        con = self._conn()
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise

    def latest_record_date(self) -> str | None:
        """Most recent sitting or answer date in the ingested files (keys end in YYYY-MM-DD)."""
        with self.connect() as con:
            row = con.execute("SELECT max(substr(key, -10)) FROM manifest "
                              "WHERE key GLOB '*[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'").fetchone()
        return row[0] if row else None

    # manifest
    def manifest(self) -> dict[tuple[str, str], sqlite3.Row]:
        with self.connect() as con:
            return {(r["source"], r["key"]): r for r in con.execute("SELECT * FROM manifest")}

    def upsert_manifest(self, row: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO manifest (source, key, revision, file_name, size, mtime_ns, rows, parsed_at, indexed_at)
                   VALUES (:source, :key, :revision, :file_name, :size, :mtime_ns, :rows, :parsed_at, NULL)
                   ON CONFLICT(source, key) DO UPDATE SET revision=excluded.revision, file_name=excluded.file_name,
                     size=excluded.size, mtime_ns=excluded.mtime_ns, rows=excluded.rows,
                     parsed_at=excluded.parsed_at, indexed_at=NULL, embedded_at=NULL""",
                row,
            )

    def mark_indexed(self, keys: list[tuple[str, str]]) -> None:
        """Indexing a file replaces its passages and drops its vectors, so it needs embedding again."""
        now = utcnow()
        with self.connect() as con:
            con.executemany("UPDATE manifest SET indexed_at=?, embedded_at=NULL WHERE source=? AND key=?",
                            [(now, s, k) for s, k in keys])

    def mark_embedded(self, keys: list[tuple[str, str]]) -> None:
        now = utcnow()
        with self.connect() as con:
            con.executemany("UPDATE manifest SET embedded_at=? WHERE source=? AND key=?",
                            [(now, s, k) for s, k in keys])

    def unembedded(self, newest_first: bool = True) -> list[tuple[str, str]]:
        order = "DESC" if newest_first else "ASC"
        with self.connect() as con:
            return [(r[0], r[1]) for r in con.execute(
                "SELECT source, key FROM manifest WHERE indexed_at IS NOT NULL AND embedded_at IS NULL "
                f"AND source NOT LIKE '%division%' ORDER BY substr(key, -10) {order}, source, key")]

    def unindexed(self) -> list[tuple[str, str]]:
        with self.connect() as con:
            return [(r[0], r[1]) for r in con.execute(
                "SELECT source, key FROM manifest WHERE indexed_at IS NULL AND source NOT LIKE '%division%' "
                "ORDER BY source, key")]

    # sync log
    def start_run(self, kind: str) -> int:
        with self.connect() as con:
            cur = con.execute("INSERT INTO sync_log (kind, started_at) VALUES (?, ?)", (kind, utcnow()))
            return int(cur.lastrowid)

    def close_unfinished_runs(self) -> int:
        """Mark runs left 'running' by a process that was killed (only one update runs at a time)."""
        with self.connect() as con:
            cur = con.execute("UPDATE sync_log SET status='failed', finished_at=?, "
                              "error='stopped before finishing (killed, perhaps out of memory)' "
                              "WHERE status='running'", (utcnow(),))
            return cur.rowcount

    def finish_run(self, run_id: int, stats: dict[str, Any], error: str | None = None) -> None:
        with self.connect() as con:
            con.execute("UPDATE sync_log SET finished_at=?, status=?, stats=?, error=? WHERE run_id=?",
                        (utcnow(), "failed" if error else "ok", json.dumps(stats, default=str), error, run_id))

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM sync_log ORDER BY run_id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d["stats"]) if d["stats"] else {}
            out.append(d)
        return out

    # key/value
    def get(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
