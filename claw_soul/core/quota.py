"""
Per-user quotas — disk usage + daily message budget.

These cheap checks live in their own module so anywhere that wants to gate
a runaway operation (selfie spam, write_file bombs, chat-flooding) can call
``check_disk()`` or ``check_messages()`` without dragging in a heavy
SessionManager / billing context.

Both quotas are cached in-process for performance; cache TTL is short
(15-60 s) so a real-time check after a write still picks up the delta.

Config keys (under ``quota`` in claw_soul.json):

    "quota": {
        "diskMb": 200,             # per-user volume budget
        "dailyMessages": 200,      # per-user chat messages per UTC day
        "checkEverySeconds": 30    # caching TTL
    }
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import config
from . import plans, tenancy

logger = logging.getLogger(__name__)


DEFAULT_DISK_MB = 200
DEFAULT_DAILY_MESSAGES = 200
DEFAULT_CACHE_TTL = 30


@dataclass
class QuotaState:
    disk_bytes: int = 0
    messages_today: int = 0
    last_check: float = 0.0
    last_day: str = ""


# Process-wide cache keyed by tenant home path
_STATE: dict[str, QuotaState] = {}


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _disk_mb_limit() -> int:
    return int(config.get_int("quota", "diskMb", default=DEFAULT_DISK_MB) or DEFAULT_DISK_MB)


def _daily_messages_limit() -> int:
    return int(
        config.get_int("quota", "dailyMessages", default=DEFAULT_DAILY_MESSAGES)
        or DEFAULT_DAILY_MESSAGES
    )


def _ttl() -> int:
    return int(
        config.get_int("quota", "checkEverySeconds", default=DEFAULT_CACHE_TTL)
        or DEFAULT_CACHE_TTL
    )


def _home() -> str:
    return os.path.realpath(str(config.CLAWSOUL_HOME))


def _state() -> QuotaState:
    """Get or create the quota state for the current tenant."""
    home = _home()
    state = _STATE.get(home)
    if state is None:
        state = _STATE[home] = QuotaState()
    today = _today_key()
    if state.last_day != today:
        state.messages_today = 0
        state.last_day = today
    return state


# ── Disk usage ──────────────────────────────────────────────────────────────

def _measure_disk(home: str) -> int:
    """Walk the tenant home and sum file sizes (cheap enough at MB-scale)."""
    total = 0
    for root, _dirs, files in os.walk(home):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def disk_status() -> dict[str, Any]:
    """Return current usage + limit. Refreshes the cache if TTL expired."""
    state = _state()
    home = _home()
    if time.monotonic() - state.last_check > _ttl():
        state.disk_bytes = _measure_disk(home) if os.path.isdir(home) else 0
        state.last_check = time.monotonic()
    limit_b = _disk_mb_limit() * 1024 * 1024
    return {
        "used_bytes": state.disk_bytes,
        "limit_bytes": limit_b,
        "used_mb": round(state.disk_bytes / 1024 / 1024, 2),
        "limit_mb": _disk_mb_limit(),
        "over": state.disk_bytes >= limit_b,
    }


def check_disk(extra_bytes: int = 0) -> str | None:
    """Return an error string if writing *extra_bytes* would exceed the quota.

    Call this BEFORE the actual write — once you have a reasonable estimate
    of how big the new file is. Returns ``None`` when the write is allowed.
    """
    s = disk_status()
    if s["used_bytes"] + extra_bytes >= s["limit_bytes"]:
        return (
            f"Disk quota exceeded: used {s['used_mb']} MB of {s['limit_mb']} MB. "
            "Old photos auto-prune at 30 days; delete unneeded files to free space."
        )
    return None


# ── Pg-backed daily count (survives worker cold-boot) ────────────────────────
#
# The in-memory counter resets every time the worker idle-suspends, which
# defeats the daily cap.  When Supabase is configured and a tenant is bound
# we persist the count in Postgres (migration 011) with an atomic-increment
# RPC.  Single-tenant / dev installs with no Supabase fall back to the
# in-memory counter unchanged.

def _pg_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _pg_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _pg_quota_uid() -> str | None:
    """Tenant to scope the Pg quota to, or None to use in-memory state."""
    if not (_pg_url() and _pg_key()):
        return None
    return tenancy.get_current_user()


def _pg_headers() -> dict[str, str]:
    return {
        "apikey": _pg_key(),
        "Authorization": f"Bearer {_pg_key()}",
        "Content-Type": "application/json",
    }


def _pg_count_today(uid: str) -> int | None:
    """Read today's message count from Pg, or None on error (caller falls
    back to in-memory rather than silently reporting 0)."""
    try:
        r = httpx.get(
            f"{_pg_url()}/rest/v1/daily_quota",
            params={"user_id": f"eq.{uid}", "day": f"eq.{_today_key()}",
                    "select": "messages"},
            headers=_pg_headers(), timeout=5,
        )
        if not r.is_success:
            logger.warning("[quota] Pg count read failed: %s", r.status_code)
            return None
        rows = r.json() or []
        return int(rows[0]["messages"]) if rows else 0
    except Exception as exc:
        logger.warning("[quota] Pg count read errored: %s", exc)
        return None


def _pg_increment_today(uid: str) -> int | None:
    """Atomically increment today's count in Pg; return the new count, or
    None on error."""
    try:
        r = httpx.post(
            f"{_pg_url()}/rest/v1/rpc/increment_daily_messages",
            json={"p_user_id": uid, "p_day": _today_key()},
            headers=_pg_headers(), timeout=5,
        )
        if not r.is_success:
            logger.warning("[quota] Pg increment failed: %s %s",
                           r.status_code, r.text[:200])
            return None
        return int(r.json())
    except Exception as exc:
        logger.warning("[quota] Pg increment errored: %s", exc)
        return None


# ── Tier resolution + monthly counters ──────────────────────────────────────

_TIER_CACHE: dict[str, tuple[float, str]] = {}   # uid -> (monotonic_ts, tier)
_TIER_TTL = 120


def _current_tier() -> str:
    """The bound user's plan tier (free | pro | ultra | enterprise).

    Worker mode pins it via ``CLAW_TIER``; the web app (many tenants per
    process) looks it up from ``user_machines.tier``, cached briefly."""
    env_tier = os.environ.get("CLAW_TIER", "").strip()
    if env_tier:
        return plans.normalize_tier(env_tier)
    uid = _pg_quota_uid()
    if not uid:
        return "free"
    hit = _TIER_CACHE.get(uid)
    if hit and (time.monotonic() - hit[0]) < _TIER_TTL:
        return hit[1]
    tier = "free"
    try:
        r = httpx.get(f"{_pg_url()}/rest/v1/user_machines",
                      params={"user_id": f"eq.{uid}", "select": "tier"},
                      headers=_pg_headers(), timeout=5)
        if r.is_success and r.json():
            tier = plans.normalize_tier(r.json()[0].get("tier"))
    except Exception:
        pass
    _TIER_CACHE[uid] = (time.monotonic(), tier)
    return tier


def _month_start() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-01")


def _pg_count_month(uid: str) -> int | None:
    """Sum messages across the current UTC month (daily_quota rows)."""
    try:
        r = httpx.get(f"{_pg_url()}/rest/v1/daily_quota",
                      params={"user_id": f"eq.{uid}", "day": f"gte.{_month_start()}",
                              "select": "messages"},
                      headers=_pg_headers(), timeout=6)
        if not r.is_success:
            return None
        return sum(int(x.get("messages") or 0) for x in (r.json() or []))
    except Exception:
        return None


def _pg_photo_count_month(uid: str) -> int | None:
    """Count photos generated this UTC month (the ``photos`` table)."""
    try:
        r = httpx.get(f"{_pg_url()}/rest/v1/photos",
                      params={"user_id": f"eq.{uid}",
                              "ts": f"gte.{_month_start()}T00:00:00Z",
                              "select": "filename"},
                      headers={**_pg_headers(), "Prefer": "count=exact", "Range": "0-0"},
                      timeout=6)
        cr = r.headers.get("content-range") or ""
        if "/" in cr:
            return int(cr.rsplit("/", 1)[1])
    except Exception:
        pass
    return None


def _is_zh() -> bool:
    return (config.get_str("agent", "language", default="en") or "en").lower().startswith("zh")


# ── Message budget (tier-aware: free = monthly, paid = daily fair-use) ───────

def message_status() -> dict[str, Any]:
    """Usage vs the tier's message cap.  Free is a monthly cap; paid tiers a
    daily fair-use ceiling.  Reads from Pg when configured (survives worker
    cold-boot), else in-memory."""
    tier = _current_tier()
    period, limit = plans.message_cap(tier)
    uid = _pg_quota_uid()
    used = None
    if uid:
        used = _pg_count_month(uid) if period == "month" else _pg_count_today(uid)
    if used is None:
        used = _state().messages_today
    return {
        "tier": tier, "period": period, "used": used, "limit": limit,
        "remaining": max(0, limit - used), "over": used >= limit,
    }


def check_messages() -> str | None:
    """In-character refusal (shown to the user verbatim) when the message cap
    is hit — warm, no system jargon.  Returns None when chat is allowed."""
    s = message_status()
    if not s["over"]:
        return None
    if s["period"] == "month":
        return ("这个月的免费消息用完啦 💕 想现在继续聊就升级，不然下个月初我就回来找你～"
                if _is_zh() else
                "we've used up this month's free messages 💕 upgrade to keep chatting "
                "now, or I'll be right here when it resets next month.")
    return ("今天聊得好多呀 😅 剩下的明天再聊好不好，我哪也不去～"
            if _is_zh() else
            "we've talked SO much today 😅 let's pick this back up tomorrow, ok? "
            "I'm not going anywhere.")


def record_message() -> None:
    """Increment the daily counter (monthly totals are summed from these)."""
    uid = _pg_quota_uid()
    if uid:
        _pg_increment_today(uid)
    _state().messages_today += 1


# ── Photo budget (monthly, tier-aware) ──────────────────────────────────────

def photo_status() -> dict[str, Any]:
    tier = _current_tier()
    limit = plans.photo_cap(tier)
    uid = _pg_quota_uid()
    used = _pg_photo_count_month(uid) if uid else 0
    if used is None:
        used = 0
    return {"tier": tier, "used": used, "limit": limit,
            "remaining": max(0, limit - used), "over": used >= limit}


def check_photos() -> str | None:
    """In-character refusal when the monthly photo cap is hit.  Call BEFORE
    spending a Seedream generation."""
    if not photo_status()["over"]:
        return None
    return ("这个月的照片额度用完啦 🥺 想现在多看就升级，不然下个月再给你拍新的～"
            if _is_zh() else
            "I'm all out of photos for this month 🥺 upgrade to send more now, "
            "or I'll have fresh ones for you next month.")


def reset_for_tests() -> None:
    """Drop all cached state — for tests that switch tenants in one process."""
    _STATE.clear()
