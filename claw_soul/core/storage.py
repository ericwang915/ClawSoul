"""
StorageManager — single SQLite DB for every event-stream-type record.

Replaces / unifies what used to be:
  - ``context/logs/history_detail.jsonl``   (every chat tool call / response)
  - ``context/sessions.db``                 (verbatim user / assistant turns)

Layout
------
``~/.claw_soul/context/claw_soul.db`` — single SQLite file, WAL mode.

Tables
------
  events       Generic event log (kind + payload JSON).  Append-only writes,
               age-based retention (default 90 days).
  turns        User / assistant chat turns mirrored from PersistentAgent.
               Long retention (default 365 days).  Backed by turns_fts FTS5
               (trigram tokenizer) for cross-session full-text recall.

Why not split into multiple DBs?  Because there is exactly one writer (the
running daemon / CLI), and a single DB makes ``status`` / ``prune`` /
``VACUUM`` trivial to reason about.

Why keep emotional_graph and daily memory logs out?  They have richer typed
structure than fits a generic event table, and are already capped.  See the
module docstring of ``memory/emotional_graph.py`` and the daily-log writer in
``memory/storage.py`` for those policies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from .. import config

logger = logging.getLogger(__name__)


# ── Defaults (overridable via the `storage.*` config section) ───────────────

DEFAULT_EVENTS_RETENTION_DAYS = 90
DEFAULT_TURNS_RETENTION_DAYS = 365
DEFAULT_AUTO_VACUUM_ON_PRUNE = True


def _db_path() -> str:
    return os.path.join(str(config.CLAWSOUL_HOME), "context", "claw_soul.db")


@dataclass
class TurnHit:
    session_id: str
    role: str
    content: str
    ts: str
    snippet: str


# ── StorageManager ──────────────────────────────────────────────────────────


class StorageManager:
    """Unified SQLite store.  Thread-safe (one connection per thread).

    Multi-tenant: ``instance()`` resolves the *current tenant's* DB path each
    call and returns a per-path manager.  Two tenants get two separate
    managers backed by two separate DB files; within a tenant, all callers
    share one manager (with thread-local sqlite connections).
    """

    _by_path: "dict[str, StorageManager]" = {}
    _lock = threading.Lock()

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _db_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._tlocal = threading.local()
        self._init_schema()

    # ── Per-tenant accessor (preferred) ────────────────────────────────

    @classmethod
    def instance(cls) -> "StorageManager":
        path = _db_path()  # resolves per-tenant via config.CLAWSOUL_HOME
        existing = cls._by_path.get(path)
        if existing is not None:
            return existing
        with cls._lock:
            existing = cls._by_path.get(path)
            if existing is None:
                existing = cls(path)
                cls._by_path[path] = existing
        return existing

    @classmethod
    def reset_for_tests(cls) -> None:
        """Drop the cached per-path managers (so tests can switch CLAWSOUL_HOME)."""
        with cls._lock:
            for mgr in list(cls._by_path.values()):
                mgr.close()
            cls._by_path.clear()

    # ── Connections ─────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._tlocal, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(self.path, isolation_level=None)  # autocommit
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._tlocal.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._tlocal, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._tlocal.conn = None

    # ── Schema ──────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn().executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            session_id  TEXT,
            kind        TEXT NOT NULL,
            payload     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);

        CREATE TABLE IF NOT EXISTS turns (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            role         TEXT NOT NULL,
            content      TEXT NOT NULL,
            ts           TEXT NOT NULL,
            content_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_turns_session_ts ON turns(session_id, ts);
        CREATE INDEX IF NOT EXISTS idx_turns_hash ON turns(content_hash);
        CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);

        CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
            content, role, session_id,
            content='turns', content_rowid='id',
            tokenize='trigram'
        );
        CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
            INSERT INTO turns_fts(rowid, content, role, session_id)
            VALUES (new.id, new.content, new.role, new.session_id);
        END;
        CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
            INSERT INTO turns_fts(turns_fts, rowid, content, role, session_id)
            VALUES('delete', old.id, old.content, old.role, old.session_id);
        END;
        """)

    # ── Retention config (read fresh each time so config changes take effect) ─

    @staticmethod
    def _events_retention_days() -> int:
        return int(config.get(
            "storage", "eventsRetentionDays",
            default=DEFAULT_EVENTS_RETENTION_DAYS,
        ) or DEFAULT_EVENTS_RETENTION_DAYS)

    @staticmethod
    def _turns_retention_days() -> int:
        return int(config.get(
            "storage", "turnsRetentionDays",
            default=DEFAULT_TURNS_RETENTION_DAYS,
        ) or DEFAULT_TURNS_RETENTION_DAYS)

    @staticmethod
    def _auto_vacuum() -> bool:
        v = config.get(
            "storage", "autoVacuumOnPrune",
            default=DEFAULT_AUTO_VACUUM_ON_PRUNE,
        )
        return bool(v) if v is not None else DEFAULT_AUTO_VACUUM_ON_PRUNE

    # ── Events API ──────────────────────────────────────────────────────

    def log_event(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> None:
        """Append a generic event.  Cheap; safe to call on the chat hot path."""
        try:
            self._conn().execute(
                "INSERT INTO events(ts, session_id, kind, payload) VALUES (?, ?, ?, ?)",
                (
                    datetime.now().isoformat(timespec="milliseconds"),
                    session_id,
                    kind,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
        except sqlite3.Error as exc:
            logger.warning("[Storage] log_event failed: %s", exc)

    def recent_events(
        self,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT id, ts, session_id, kind, payload FROM events"
        params: list = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._conn().execute(sql, params).fetchall()
        return [
            {
                "id": r[0], "ts": r[1], "session_id": r[2],
                "kind": r[3], "payload": json.loads(r[4]),
            }
            for r in rows
        ]

    # ── Turns API (mirrors what old SessionIndex did) ───────────────────

    def index_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        ts: str | None = None,
    ) -> bool:
        if role not in ("user", "assistant"):
            return False
        text = (content or "").strip()
        if not text:
            return False

        h = hashlib.sha1(f"{session_id}|{role}|{text}".encode("utf-8")).hexdigest()
        ts = ts or datetime.now().isoformat(timespec="seconds")

        conn = self._conn()
        row = conn.execute(
            "SELECT 1 FROM turns WHERE content_hash = ? LIMIT 1", (h,)
        ).fetchone()
        if row:
            return False
        conn.execute(
            "INSERT INTO turns(session_id, role, content, ts, content_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, ts, h),
        )
        return True

    def index_messages(self, session_id: str, messages: Iterable[dict]) -> int:
        added = 0
        for m in messages:
            content = m.get("content")
            if not isinstance(content, str):
                continue
            if self.index_turn(session_id, m.get("role", ""), content, ts=m.get("_ts")):
                added += 1
        return added

    @staticmethod
    def _clean_query(query: str) -> list[str]:
        cleaned = (query or "").translate(str.maketrans('"\'()[]{}:', "         "))
        tokens = [t.strip() for t in cleaned.split() if t.strip()]
        return [t for t in tokens if t not in ("AND", "OR", "NOT", "NEAR")]

    def search_turns(
        self,
        query: str,
        k: int = 5,
        since: str | None = None,
        session_id: str | None = None,
    ) -> list[TurnHit]:
        tokens = self._clean_query(query)
        if not tokens:
            return []

        # Stage 1: FTS5 trigram (needs ≥3-char tokens)
        if all(len(t) >= 3 for t in tokens):
            fts_query = " AND ".join(
                f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens
            )
            hits = self._fts_search(fts_query, k, since, session_id)
            if hits:
                return hits

        # Stage 2: LIKE fallback for short CN queries
        return self._like_search(tokens, k, since, session_id)

    def _fts_search(self, fts_query, k, since, session_id) -> list[TurnHit]:
        sql = """
        SELECT t.session_id, t.role, t.content, t.ts,
               snippet(turns_fts, 0, '«', '»', '…', 20) AS snip
        FROM turns_fts
        JOIN turns t ON t.id = turns_fts.rowid
        WHERE turns_fts MATCH ?
        """
        params: list = [fts_query]
        if since:
            sql += " AND t.ts >= ?"
            params.append(since)
        if session_id:
            sql += " AND t.session_id = ?"
            params.append(session_id)
        sql += " ORDER BY rank LIMIT ?"
        params.append(k)
        rows = self._conn().execute(sql, params).fetchall()
        return [TurnHit(*r) for r in rows]

    def _like_search(self, tokens, k, since, session_id) -> list[TurnHit]:
        sql = "SELECT session_id, role, content, ts FROM turns WHERE 1=1"
        params: list = []
        for t in tokens:
            sql += " AND content LIKE ?"
            params.append(f"%{t}%")
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(k)
        rows = self._conn().execute(sql, params).fetchall()
        hits: list[TurnHit] = []
        for r in rows:
            content = r[2]
            snip = content
            for t in tokens:
                pos = content.find(t)
                if pos >= 0:
                    start, end = max(0, pos - 20), min(len(content), pos + len(t) + 20)
                    snip = ("…" if start > 0 else "") + content[start:pos] + \
                           "«" + content[pos:pos+len(t)] + "»" + \
                           content[pos+len(t):end] + ("…" if end < len(content) else "")
                    break
            hits.append(TurnHit(session_id=r[0], role=r[1], content=content, ts=r[3], snippet=snip))
        return hits

    # ── Privacy / session control ───────────────────────────────────────

    def clear_session(self, session_id: str) -> int:
        """Delete every turn for a session.  Used by `clear_chat_history`
        so a wiped session is also gone from FTS recall.  Returns rows removed."""
        cur = self._conn().execute(
            "DELETE FROM turns WHERE session_id = ?", (session_id,)
        )
        n = cur.rowcount or 0
        if n:
            logger.info("[Storage] cleared %d turns for session %s", n, session_id)
        return n

    # ── Maintenance: status / prune / VACUUM ────────────────────────────

    def status(self) -> dict[str, Any]:
        """Counts, sizes, oldest/newest timestamps, current retention policy."""
        conn = self._conn()
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        turns_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        events_oldest = conn.execute("SELECT MIN(ts) FROM events").fetchone()[0]
        events_newest = conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]
        turns_oldest = conn.execute("SELECT MIN(ts) FROM turns").fetchone()[0]
        turns_newest = conn.execute("SELECT MAX(ts) FROM turns").fetchone()[0]

        size_bytes = 0
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.isfile(p):
                size_bytes += os.path.getsize(p)

        return {
            "path": self.path,
            "size_bytes": size_bytes,
            "events": {
                "count": events_count,
                "oldest": events_oldest,
                "newest": events_newest,
                "retention_days": self._events_retention_days(),
            },
            "turns": {
                "count": turns_count,
                "oldest": turns_oldest,
                "newest": turns_newest,
                "retention_days": self._turns_retention_days(),
            },
        }

    def prune(self, dry_run: bool = False) -> dict[str, Any]:
        """Delete rows past retention; optionally VACUUM afterwards.

        Returns a dict like ``{events_deleted, turns_deleted, vacuumed}``.
        """
        events_cutoff = (
            datetime.now() - timedelta(days=self._events_retention_days())
        ).isoformat(timespec="seconds")
        turns_cutoff = (
            datetime.now() - timedelta(days=self._turns_retention_days())
        ).isoformat(timespec="seconds")

        conn = self._conn()
        events_to_del = conn.execute(
            "SELECT COUNT(*) FROM events WHERE ts < ?", (events_cutoff,)
        ).fetchone()[0]
        turns_to_del = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE ts < ?", (turns_cutoff,)
        ).fetchone()[0]

        if dry_run:
            return {
                "dry_run": True,
                "events_deleted": events_to_del,
                "turns_deleted": turns_to_del,
                "vacuumed": False,
            }

        if events_to_del:
            conn.execute("DELETE FROM events WHERE ts < ?", (events_cutoff,))
        if turns_to_del:
            conn.execute("DELETE FROM turns WHERE ts < ?", (turns_cutoff,))

        vacuumed = False
        if (events_to_del or turns_to_del) and self._auto_vacuum():
            try:
                conn.execute("VACUUM")
                vacuumed = True
            except sqlite3.Error as exc:
                logger.warning("[Storage] VACUUM failed: %s", exc)

        logger.info(
            "[Storage] prune complete: events=-%d, turns=-%d, vacuumed=%s",
            events_to_del, turns_to_del, vacuumed,
        )
        return {
            "dry_run": False,
            "events_deleted": events_to_del,
            "turns_deleted": turns_to_del,
            "vacuumed": vacuumed,
        }
