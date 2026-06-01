"""
Postgres-backed sessions / turns / events / memory for SaaS workers.

Mirrors the public surface of:
  - :class:`claw_soul.core.session_store.SessionStore`
  - :class:`claw_soul.core.storage.StorageManager`
  - :class:`claw_soul.core.memory.manager.MemoryManager`

…but persists to Supabase Postgres tables (see migration 004).  Worker
machines use these when ``CLAW_USE_POSTGRES=1`` so the local SQLite +
markdown files become optional — cold-booted workers can read everything
fresh from the central DB and immediately serve traffic.

Falls back gracefully: if Supabase env vars are missing, all calls
become no-ops (logged) so a misconfigured worker doesn't crash —
it just degrades to a stateless agent.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from ..core import tenancy

logger = logging.getLogger(__name__)


# ── Common transport ──────────────────────────────────────────────────

def _url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _configured() -> bool:
    return bool(_url() and _key())


def _headers(*, prefer: str = "return=representation") -> dict[str, str]:
    return {
        "apikey": _key(),
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _current_user_id() -> str:
    uid = tenancy.get_current_user()
    if not uid:
        raise RuntimeError("storage_pg called without a bound tenant")
    return uid


# Shared connection-pooled client — reused across every turn save/load and
# memory read so we don't pay a fresh TCP+TLS handshake per PostgREST call.
# httpx.Client is thread-safe; storage_pg runs from the (serialized) agent
# thread plus background tasks, so a single process-wide client is correct.
_CLIENT: httpx.Client | None = None


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=15)
    return _CLIENT


def _post(path: str, body: dict | list, *, params: dict | None = None,
          prefer: str = "return=minimal") -> dict | list | None:
    if not _configured():
        return None
    r = _client().post(_url() + path, json=body, params=params or {},
                       headers=_headers(prefer=prefer))
    if not r.is_success:
        logger.warning("[storage_pg] POST %s → %s %s", path, r.status_code, r.text[:200])
        return None
    return r.json() if r.text else None


def _get(path: str, params: dict | None = None) -> list[dict]:
    if not _configured():
        return []
    r = _client().get(_url() + path, params=params or {}, headers=_headers())
    if not r.is_success:
        logger.warning("[storage_pg] GET %s → %s %s", path, r.status_code, r.text[:200])
        return []
    return r.json() or []


def _delete(path: str, params: dict | None = None) -> bool:
    if not _configured():
        return False
    r = _client().delete(_url() + path, params=params or {}, headers=_headers())
    return r.is_success


# ── SessionStore (Pg) ────────────────────────────────────────────────


class SessionStorePg:
    """Drop-in for the markdown-based SessionStore, persisting to Postgres.

    Public surface kept lean: ``save(session_id, messages)`` + ``load(session_id)``.
    """

    # save() already batch-writes every turn into the shared `turns` table
    # (one POST), which is the same table StorageManagerPg.search_turns
    # reads.  So PersistentAgent can skip the separate per-message index
    # mirror in Pg mode — see PersistentAgent._save.
    writes_turn_index: bool = True

    def save(self, session_id: str, messages: list[dict]) -> None:
        uid = _current_user_id()
        _post("/rest/v1/sessions", {
            "user_id": uid, "session_id": session_id,
            "updated_at": _now_iso(), "message_count": len(messages),
        }, params={"on_conflict": "user_id,session_id"},
           prefer="resolution=merge-duplicates,return=minimal")

        # Append-only model: just upsert turns we haven't seen by content_hash.
        rows: list[dict] = []
        for m in messages:
            if m.get("role") not in ("user", "assistant"):
                continue
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            ts = m.get("_ts") or _now_iso()
            h = _hash_turn(uid, session_id, m["role"], content)
            rows.append({
                "user_id": uid, "session_id": session_id,
                "role": m["role"], "content": content,
                "ts": ts, "content_hash": h,
            })
        if rows:
            _post("/rest/v1/turns", rows,
                  params={"on_conflict": "user_id,content_hash"},
                  prefer="resolution=ignore-duplicates,return=minimal")

    def load(self, session_id: str) -> list[dict]:
        uid = _current_user_id()
        rows = _get("/rest/v1/turns", params={
            "user_id":    f"eq.{uid}",
            "session_id": f"eq.{session_id}",
            "select":     "role,content,ts",
            "order":      "ts.desc",   # newest first, so the limit keeps RECENT turns
            "limit":      "200",       # 200 turns ≈ 100 rounds (1 round = user + assistant)
        })
        rows = list(reversed(rows))    # back to chronological (ts.asc) for the agent
        return [
            {"role": r["role"], "content": r["content"], "_ts": r["ts"]}
            for r in rows
        ]

    def delete(self, session_id: str) -> int:
        uid = _current_user_id()
        ok1 = _delete("/rest/v1/turns",
                      params={"user_id": f"eq.{uid}",
                              "session_id": f"eq.{session_id}"})
        ok2 = _delete("/rest/v1/sessions",
                      params={"user_id": f"eq.{uid}",
                              "session_id": f"eq.{session_id}"})
        return int(ok1) + int(ok2)


# ── Storage / event log (Pg) ──────────────────────────────────────────


@dataclass
class TurnHit:
    session_id: str
    role: str
    content: str
    ts: str
    snippet: str


class StorageManagerPg:
    """Mirror of :class:`claw_soul.core.storage.StorageManager` against
    Postgres.  Per-tenant scoping comes from the tenancy contextvar."""

    # Singleton — exactly one instance per worker process (the worker is
    # already pinned to one tenant via CLAW_USER_ID).
    _instance: "StorageManagerPg | None" = None

    @classmethod
    def instance(cls) -> "StorageManagerPg":
        if cls._instance is None:
            cls._instance = StorageManagerPg()
        return cls._instance

    # Events ──────────────────────────────────────────────────────────

    def log_event(self, kind: str, payload: dict[str, Any] | None = None,
                  session_id: str | None = None) -> None:
        uid = _current_user_id()
        _post("/rest/v1/events", {
            "user_id": uid, "session_id": session_id, "kind": kind,
            "payload": payload or {},
        }, prefer="return=minimal")

    def recent_events(self, kind: str | None = None, limit: int = 100) -> list[dict]:
        uid = _current_user_id()
        params = {
            "user_id": f"eq.{uid}",
            "select":  "id,ts,session_id,kind,payload",
            "order":   "ts.desc",
            "limit":   str(min(max(1, limit), 500)),
        }
        if kind:
            params["kind"] = f"eq.{kind}"
        return _get("/rest/v1/events", params=params)

    # Turns ───────────────────────────────────────────────────────────

    def index_turn(self, session_id: str, role: str, content: str,
                   ts: str | None = None) -> bool:
        if role not in ("user", "assistant"):
            return False
        text = (content or "").strip()
        if not text:
            return False
        uid = _current_user_id()
        h = _hash_turn(uid, session_id, role, text)
        res = _post("/rest/v1/turns", {
            "user_id": uid, "session_id": session_id,
            "role": role, "content": text,
            "ts": ts or _now_iso(), "content_hash": h,
        }, params={"on_conflict": "user_id,content_hash"},
           prefer="resolution=ignore-duplicates,return=minimal")
        return res is not None

    def index_messages(self, session_id: str, messages: Iterable[dict]) -> int:
        added = 0
        for m in messages:
            content = m.get("content")
            if not isinstance(content, str):
                continue
            if self.index_turn(session_id, m.get("role", ""), content,
                               ts=m.get("_ts")):
                added += 1
        return added

    def search_turns(self, query: str, *, k: int = 5,
                     since: str | None = None,
                     session_id: str | None = None) -> list[TurnHit]:
        uid = _current_user_id()
        tokens = _clean_query(query)
        if not tokens:
            return []
        # Use ilike on the joined %-padded query; pg_trgm GIN supports it.
        # PostgREST `or=` is verbose; simpler approach: one ilike per token AND-chained.
        params: dict[str, str] = {
            "user_id": f"eq.{uid}",
            "select":  "session_id,role,content,ts",
            "order":   "ts.desc",
            "limit":   str(min(max(1, k), 50)),
        }
        for t in tokens:
            params.setdefault("content", []).append(f"ilike.%{_escape_like(t)}%")  # type: ignore[arg-type]
        if since:
            params["ts"] = f"gte.{since}"
        if session_id:
            params["session_id"] = f"eq.{session_id}"
        # PostgREST accepts repeated query params by passing a list when using httpx
        rows = _get("/rest/v1/turns", params=params)
        hits: list[TurnHit] = []
        for r in rows:
            snip = _snippet(r["content"], tokens)
            hits.append(TurnHit(
                session_id=r["session_id"], role=r["role"],
                content=r["content"], ts=r["ts"], snippet=snip,
            ))
        return hits

    def clear_session(self, session_id: str) -> int:
        uid = _current_user_id()
        ok = _delete("/rest/v1/turns", params={
            "user_id": f"eq.{uid}", "session_id": f"eq.{session_id}",
        })
        return 1 if ok else 0

    # Maintenance (no-ops in Pg — let Postgres handle vacuuming) ─────

    def status(self) -> dict[str, Any]:
        return {"backend": "postgres", "user_id": _current_user_id()}

    def prune(self, dry_run: bool = False) -> dict[str, Any]:
        # Retention is a separate cron job that runs DELETE ... WHERE ts < now() - interval
        # against Postgres directly. Keep this stub so the interface matches.
        return {"backend": "postgres", "dry_run": dry_run, "note": "use SQL cron"}


# ── Memory (Pg) ───────────────────────────────────────────────────────


class MemoryManagerPg:
    """Mirror of MemoryManager — Postgres-backed key/value + daily log.

    Public methods kept to the ones agent.py + tools.py call:
      - list_all() → dict[key, content]
      - remember(content, key)
      - recall(query)
      - memory_get(path)            (legacy compat: ignored, returns "")
      - list_files()                (legacy compat: returns [])
      - forget(key)
      - boot_context(max_chars=…)
    """

    def list_all(self) -> dict[str, str]:
        uid = _current_user_id()
        rows = _get("/rest/v1/memory_entries", params={
            "user_id": f"eq.{uid}",
            "select":  "key,content",
            "limit":   "500",
        })
        return {r["key"]: r["content"] for r in rows}

    def remember(self, content: str, key: str | None = None) -> str:
        uid = _current_user_id()
        k = (key or "").strip() or _autokey(content)
        _post("/rest/v1/memory_entries", {
            "user_id": uid, "key": k, "content": content,
        }, params={"on_conflict": "user_id,key"},
           prefer="resolution=merge-duplicates,return=minimal")
        today = datetime.now(timezone.utc).date().isoformat()
        _post("/rest/v1/memory_daily", {
            "user_id": uid, "day": today, "key": k, "content": content,
        }, prefer="return=minimal")
        return f"Remembered '{k}'."

    def recall(self, query: str) -> str:
        uid = _current_user_id()
        if not query or query == "*":
            rows = _get("/rest/v1/memory_entries", params={
                "user_id": f"eq.{uid}", "select": "key,content",
                "order": "updated_at.desc", "limit": "100",
            })
        else:
            tokens = _clean_query(query)
            if not tokens:
                return "(empty query)"
            params: dict[str, Any] = {
                "user_id": f"eq.{uid}", "select": "key,content",
                "limit": "20",
            }
            for t in tokens:
                params.setdefault("content", []).append(f"ilike.%{_escape_like(t)}%")
            rows = _get("/rest/v1/memory_entries", params=params)
        if not rows:
            return "(no memories matched)"
        return "\n".join(f"- {r['key']}: {r['content']}" for r in rows)

    def memory_get(self, path: str) -> str:
        return ""  # legacy markdown path not applicable in Pg mode

    def list_files(self) -> list[str]:
        return []

    def forget(self, key: str) -> str:
        uid = _current_user_id()
        ok = _delete("/rest/v1/memory_entries", params={
            "user_id": f"eq.{uid}", "key": f"eq.{key}",
        })
        return f"Forgot '{key}'." if ok else f"No memory with key '{key}'."

    def write_index(self, content: str) -> str:
        return self.remember(content, key="INDEX")

    def boot_context(self, max_chars: int = 3000) -> str:
        all_mem = self.list_all()
        if not all_mem:
            return ""
        # Cap total length by greedily concatenating with a budget
        parts: list[str] = []
        used = 0
        for k, v in all_mem.items():
            line = f"- {k}: {v}"
            if used + len(line) + 1 > max_chars:
                break
            parts.append(line)
            used += len(line) + 1
        return "\n".join(parts)


# ── Helpers ───────────────────────────────────────────────────────────


def _hash_turn(uid: str, sid: str, role: str, text: str) -> str:
    return hashlib.sha1(f"{uid}|{sid}|{role}|{text}".encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_query(query: str) -> list[str]:
    cleaned = (query or "").translate(str.maketrans('"\'()[]{}:%', "          "))
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    return [t for t in tokens if t.upper() not in ("AND", "OR", "NOT", "NEAR")]


def _escape_like(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(content: str, tokens: list[str]) -> str:
    snip = content[:120]
    for t in tokens:
        pos = content.find(t)
        if pos >= 0:
            start, end = max(0, pos - 20), min(len(content), pos + len(t) + 20)
            snip = ("…" if start > 0 else "") + content[start:pos] + \
                   "«" + content[pos:pos + len(t)] + "»" + \
                   content[pos + len(t):end] + ("…" if end < len(content) else "")
            break
    return snip


def _autokey(content: str) -> str:
    """Cheap fallback key when the caller doesn't supply one."""
    base = content.strip().splitlines()[0][:40] or "note"
    return base.lower().replace(" ", "_")


# ── Module-level helpers ──────────────────────────────────────────────


def use_postgres() -> bool:
    """Worker reads this to decide which adapter to wire up."""
    return os.environ.get("CLAW_USE_POSTGRES", "").strip() in ("1", "true", "yes")


def make_session_store():
    """Return either the markdown SessionStore or the Pg-backed one
    depending on the ``CLAW_USE_POSTGRES`` feature flag.

    Lives here (rather than in session_store.py) so the markdown module
    has zero dependency on Postgres bits — keeps single-process / dev
    installs untouched."""
    if use_postgres() and _configured():
        logger.info("[storage_pg] using Postgres-backed SessionStore")
        return SessionStorePg()
    from .session_store import SessionStore
    return SessionStore()


def make_storage_manager():
    """Return the appropriate StorageManager singleton for this process."""
    if use_postgres() and _configured():
        return StorageManagerPg.instance()
    from .storage import StorageManager
    return StorageManager.instance()
