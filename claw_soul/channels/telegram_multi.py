"""
Multi-bot Telegram dispatcher for multi-tenant ClawSoul.

On daemon startup, queries the Supabase ``user_settings`` table for every
``(user_id, telegram_bot_token)`` pair and launches one :class:`TelegramBot`
per pair, each pinned to its owner's tenant context.

Schema expected in Supabase::

    create table user_settings (
        user_id uuid primary key references auth.users(id) on delete cascade,
        telegram_bot_token text,
        telegram_chat_id bigint,
        created_at timestamptz default now(),
        updated_at timestamptz default now()
    );

Env vars required:
    SUPABASE_URL                 https://<project>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY    service_role (NOT anon!) — used to read all rows
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from ..core import tenancy
from ..core.persistent_agent import PersistentAgent
from ..core.session_store import SessionStore
from ..session_manager import SessionManager
from .telegram_bot import TelegramBot

if TYPE_CHECKING:
    from ..core.llm.base import LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class UserBotConfig:
    user_id: str
    bot_token: str
    chat_id: int | None = None


# ── Supabase REST helpers ────────────────────────────────────────────────────

# Telegram bot token shape: `<digits>:<32+ alphanum/-/_>`
BOT_TOKEN_RE = re.compile(r"^\d{6,15}:[A-Za-z0-9_-]{30,}$")


def supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def service_role_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def supabase_configured() -> bool:
    return bool(supabase_url() and service_role_key())


# Keep the leading-underscore aliases for the existing internal call sites.
_supabase_url = supabase_url
_service_role_key = service_role_key


async def get_user_settings(user_id: str) -> dict | None:
    """Fetch one row from user_settings, or None if missing."""
    if not supabase_configured():
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{supabase_url()}/rest/v1/user_settings",
            params={
                "user_id": f"eq.{user_id}",
                "select": "telegram_bot_token,telegram_chat_id",
            },
            headers={
                "apikey": service_role_key(),
                "Authorization": f"Bearer {service_role_key()}",
            },
        )
    if not resp.is_success:
        return None
    rows = resp.json() or []
    return rows[0] if rows else None


_UNSET: object = object()


async def _find_other_user_with_token(
    user_id: str, token: str,
) -> str | None:
    """Return the user_id of any OTHER row already using *token*, else None.

    Prevents two users from both pointing at the same bot (which would cause
    Telegram getUpdates conflicts and burn the bot for both).
    """
    if not (token and supabase_configured()):
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{supabase_url()}/rest/v1/user_settings",
            params={
                "select": "user_id",
                "telegram_bot_token": f"eq.{token}",
                "user_id": f"neq.{user_id}",
            },
            headers={"apikey": service_role_key(),
                     "Authorization": f"Bearer {service_role_key()}"},
        )
    if not resp.is_success:
        return None
    rows = resp.json() or []
    return rows[0]["user_id"] if rows else None


async def upsert_user_settings(
    user_id: str,
    *,
    telegram_bot_token: object = _UNSET,
    telegram_chat_id: object = _UNSET,
) -> tuple[bool, str | None]:
    """Insert or update the user's row. Returns (ok, error_msg).

    Only fields you pass explicitly are sent — omitted kwargs leave the
    existing column untouched. Pass ``None`` to clear a field.

    Duplicate-token guard: refuses to save a Telegram token already used by
    another user.
    """
    if not supabase_configured():
        return False, "supabase not configured on the server"

    # Reject duplicate Telegram bot tokens (one bot, one user).
    if telegram_bot_token not in (_UNSET, None, ""):
        other = await _find_other_user_with_token(user_id, str(telegram_bot_token))
        if other:
            return False, (
                "This Telegram bot token is already claimed by another user. "
                "Each bot can only be paired with one ClawSoul account."
            )

    body: dict = {"user_id": user_id}
    if telegram_bot_token is not _UNSET:
        body["telegram_bot_token"] = telegram_bot_token
    if telegram_chat_id is not _UNSET:
        body["telegram_chat_id"] = telegram_chat_id

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{supabase_url()}/rest/v1/user_settings",
            params={"on_conflict": "user_id"},
            json=body,
            headers={
                "apikey": service_role_key(),
                "Authorization": f"Bearer {service_role_key()}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )

    if not resp.is_success:
        return False, f"supabase error: {resp.status_code} {resp.text[:200]}"
    return True, None


# ── Per-user integrations (api keys / oauth tokens) ─────────────────────────

async def get_user_integrations(user_id: str) -> dict:
    """Return the user's ``integrations`` JSONB blob (or empty dict)."""
    if not supabase_configured():
        return {}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{supabase_url()}/rest/v1/user_settings",
            params={"user_id": f"eq.{user_id}", "select": "integrations"},
            headers={
                "apikey": service_role_key(),
                "Authorization": f"Bearer {service_role_key()}",
            },
        )
    if not resp.is_success:
        return {}
    rows = resp.json() or []
    return (rows[0].get("integrations") or {}) if rows else {}


async def set_user_integration(
    user_id: str, name: str, payload: dict | None,
) -> tuple[bool, str | None]:
    """Merge ``payload`` into ``integrations[name]`` (or remove if None).

    Uses Postgres jsonb_set semantics by reading-then-writing — we don't
    have a transactional update endpoint via PostgREST. Race condition is
    acceptable here because each user only edits their own row from one
    browser tab at a time.
    """
    if not supabase_configured():
        return False, "supabase not configured on the server"

    current = await get_user_integrations(user_id)
    if payload is None:
        current.pop(name, None)
    else:
        current[name] = payload

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{supabase_url()}/rest/v1/user_settings",
            params={"on_conflict": "user_id"},
            json={"user_id": user_id, "integrations": current},
            headers={
                "apikey": service_role_key(),
                "Authorization": f"Bearer {service_role_key()}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
    if not resp.is_success:
        return False, f"supabase error: {resp.status_code} {resp.text[:200]}"
    return True, None


def fetch_all_user_bot_configs() -> list[UserBotConfig]:
    """Return every (user_id, bot_token) row from user_settings.

    Quietly returns an empty list if Supabase isn't configured or
    the table doesn't exist — the daemon still boots, just web-only.
    """
    url = _supabase_url()
    key = _service_role_key()
    if not url or not key:
        logger.info("[Telegram-multi] No SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY — skipping bot discovery")
        return []

    try:
        resp = httpx.get(
            f"{url}/rest/v1/user_settings",
            params={
                "select": "user_id,telegram_bot_token,telegram_chat_id",
                "telegram_bot_token": "not.is.null",
            },
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[Telegram-multi] Supabase fetch failed: %s", exc)
        return []

    rows = resp.json() or []
    configs: list[UserBotConfig] = []
    for r in rows:
        if not (r.get("user_id") and r.get("telegram_bot_token")):
            continue
        raw_chat = r.get("telegram_chat_id")
        try:
            chat_id = int(raw_chat) if raw_chat is not None else None
        except (TypeError, ValueError):
            chat_id = None
        configs.append(UserBotConfig(
            user_id=r["user_id"],
            bot_token=r["telegram_bot_token"],
            chat_id=chat_id,
        ))
    logger.info("[Telegram-multi] Loaded %d user bot(s) from Supabase", len(configs))
    return configs


# ── Per-tenant bot factory ───────────────────────────────────────────────────

def _build_tenant_bot(
    cfg: UserBotConfig,
    provider: "LLMProvider",
) -> tuple[TelegramBot, SessionManager]:
    """Construct a TelegramBot + its SessionManager, both pinned to this user's tenant.

    The session manager is returned alongside the bot so per-user scheduler
    jobs (proactive messaging, daily planner) can share it.
    """
    # Eagerly bind the tenant so the SessionManager / SessionStore created
    # below see the right CLAWSOUL_HOME at construction time.
    with tenancy.user_context(cfg.user_id):
        store = SessionStore()
        sm = SessionManager(agent_factory=lambda sid: None, store=store)

        def agent_factory(session_id: str) -> PersistentAgent:
            # Re-bind tenancy at handler-time too, in case the factory is
            # invoked from a coroutine that didn't inherit the contextvar
            # (e.g. lazy session creation).
            with tenancy.user_context(cfg.user_id):
                return PersistentAgent(
                    provider=provider,
                    store=store,
                    session_id=session_id,
                    verbose=False,
                )

        sm.set_factory(agent_factory)

    bot = TelegramBot(
        session_manager=sm,
        token=cfg.bot_token,
        tenant_user_id=cfg.user_id,
    )
    return bot, sm


# ── Public entry points ──────────────────────────────────────────────────────

def _register_user_scheduler_jobs(
    scheduler,
    cfg: UserBotConfig,
    session_manager: SessionManager,
    bot: TelegramBot,
    provider: "LLMProvider",
) -> None:
    """Register one user's daily planner + proactive + selfie jobs.

    All jobs run on the shared APScheduler, each wrapped in
    ``tenancy.user_context(user_id)`` so storage / config / memory paths
    resolve to the right tenant when the job fires.
    """
    from apscheduler.triggers.cron import CronTrigger

    user_id = cfg.user_id
    user_short = user_id[:8]

    # ── Daily planner — fires once a day at 00:01 of the user's bot tz ──────
    try:
        from ..scheduler.planner import generate_daily_plan, plan_is_stale

        wrapped_planner = tenancy.wrap_async_for_user(user_id, generate_daily_plan)
        with tenancy.user_context(user_id):
            user_tz = tenancy.bot_timezone()
        scheduler.add_job(
            wrapped_planner,
            trigger=CronTrigger(hour=0, minute=1, timezone=user_tz),
            id=f"daily_planner:{user_id}",
            args=[provider],
            replace_existing=True,
        )

        # Backfill today's plan immediately if missing, in the background.
        import asyncio
        async def _maybe_initial_plan():
            with tenancy.user_context(user_id):
                if plan_is_stale():
                    await generate_daily_plan(provider)
        asyncio.create_task(_maybe_initial_plan())

        logger.info("[Telegram-multi] Daily planner registered for user=%s", user_short)
    except Exception as exc:
        logger.warning("[Telegram-multi] Planner registration failed for user=%s: %s", user_short, exc)

    # ── Proactive messenger — 5-minute tick, per-user state ──────────────────
    def _register_proactive(chat_id: int) -> None:
        """(Re)register the proactive tick for this user.

        Spreads users across the 5-minute window via a deterministic per-user
        offset so 100 users don't all fire ``_tick`` at :00, :05, :10 …
        simultaneously (which would saturate the single vCPU and trigger
        the LLM API limiter at the same instant). offset = hash(user_id) % 5
        — same user always lands on the same minute, but the cohort spreads
        evenly across [0, 5).
        """
        import hashlib
        try:
            from ..scheduler.proactive import ProactiveMessenger
            pm = ProactiveMessenger(session_manager=session_manager, telegram_bot=bot)
            wrapped_tick = tenancy.wrap_async_for_user(user_id, pm._tick)
            offset = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 5
            scheduler.add_job(
                wrapped_tick,
                trigger=CronTrigger(minute=f"{offset}-59/5"),
                id=f"proactive:{user_id}",
                kwargs={"chat_id": chat_id},
                replace_existing=True,
            )
            logger.info(
                "[Telegram-multi] Proactive registered for user=%s chat=%s (offset=:%02d)",
                user_short, chat_id, offset,
            )
        except Exception as exc:
            logger.warning(
                "[Telegram-multi] Proactive registration failed for user=%s: %s",
                user_short, exc,
            )

    if cfg.chat_id:
        _register_proactive(cfg.chat_id)
    else:
        logger.info(
            "[Telegram-multi] Proactive deferred for user=%s — will activate "
            "on first inbound message (need chat_id)",
            user_short,
        )
        # Late-bind: the bot will call back the moment it captures chat_id
        # from an incoming message, so proactive turns on without a restart.
        bot._on_chat_id_learned = _register_proactive

    # ── Scheduled selfies — N times per day, per-user counter ────────────────
    if cfg.chat_id:
        try:
            from ..core.image_gen.selfie import is_enabled as _selfie_enabled
            with tenancy.user_context(user_id):
                selfie_on = _selfie_enabled()
            if selfie_on:
                _register_user_selfie_jobs(scheduler, user_id, bot, cfg.chat_id)
        except Exception as exc:
            logger.warning("[Telegram-multi] Selfie registration failed for user=%s: %s", user_short, exc)


def _register_user_selfie_jobs(
    scheduler,
    user_id: str,
    bot: TelegramBot,
    chat_id: int,
) -> None:
    """Register the configured scheduled-selfie slots for one user."""
    from apscheduler.triggers.cron import CronTrigger

    from .. import config as _cfg
    from ..core.image_gen import take_selfie
    from ..scheduler.selfie_task import DEFAULT_SCHEDULE

    user_short = user_id[:8]

    with tenancy.user_context(user_id):
        raw = _cfg.get_list("selfie", "schedule") or DEFAULT_SCHEDULE
        max_daily = _cfg.get_int(
            "selfie", "maxDaily", default=len(DEFAULT_SCHEDULE),
        )
        user_tz = tenancy.bot_timezone()

    slots: list[str] = []
    for t in raw:
        if isinstance(t, str) and ":" in t:
            hh, _, mm = t.partition(":")
            if hh.isdigit() and mm.isdigit():
                slots.append(f"{int(hh):02d}:{int(mm):02d}")
    slots = slots or DEFAULT_SCHEDULE

    # State per user — closure captures these.
    state = {"sent_today": 0, "last_date": None}

    async def _fire():
        import asyncio
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if state["last_date"] != today:
            state["last_date"] = today
            state["sent_today"] = 0
        if state["sent_today"] >= max_daily:
            return

        loop = asyncio.get_event_loop()
        try:
            with tenancy.user_context(user_id):
                model = _cfg.get_str("selfie", "model", default="") or None
                result = await loop.run_in_executor(
                    None, lambda: take_selfie(model=model),
                )
        except Exception as exc:
            logger.exception("[Telegram-multi] Selfie generation failed for user=%s: %s", user_short, exc)
            return

        try:
            await bot.send_photo(chat_id, result.path, caption=result.caption())
            state["sent_today"] += 1
            logger.info(
                "[Telegram-multi] Sent scheduled selfie #%d to user=%s chat=%s",
                state["sent_today"], user_short, chat_id,
            )
        except Exception as exc:
            logger.error(
                "[Telegram-multi] Telegram send failed for user=%s: %s", user_short, exc,
            )

    for slot in slots:
        hh, mm = slot.split(":")
        scheduler.add_job(
            _fire,
            trigger=CronTrigger(hour=int(hh), minute=int(mm), timezone=user_tz),
            id=f"selfie:{user_id}:{slot}",
            replace_existing=True,
        )
    logger.info(
        "[Telegram-multi] %d selfie slot(s) registered for user=%s",
        len(slots), user_short,
    )


async def start_all_user_bots(
    provider: "LLMProvider",
    *,
    scheduler=None,
) -> list[TelegramBot]:
    """Bring up one Telegram bot per user, plus per-user scheduler jobs.

    Each user gets:
      - their Telegram bot started (long-poll), pinned to their tenant
      - daily planner job (runs at 00:01, generates ``today_plan.md``)
      - proactive messenger tick (every 5 min, when they have a saved chat_id)
      - scheduled selfie slots (when Seedream is configured)

    All jobs share the global APScheduler passed in via ``scheduler`` but
    execute under ``tenancy.user_context(user_id)`` so reads/writes never
    bleed across users.

    Failures on individual bots / job registrations don't take down the
    others — they're logged and skipped.
    """
    configs = fetch_all_user_bot_configs()
    bots: list[TelegramBot] = []
    cap = _max_active_users()

    for cfg in configs:
        if len(_active_bots_by_uid) >= cap:
            logger.warning(
                "[Telegram-multi] capacity reached (%d/%d) — skipping user=%s",
                len(_active_bots_by_uid), cap, cfg.user_id[:8],
            )
            continue
        try:
            bot, sm = _build_tenant_bot(cfg, provider)
            await bot.start_async()
            bots.append(bot)
            _active_bots_by_uid[cfg.user_id] = {"bot": bot, "sm": sm, "cfg": cfg}
            logger.info("[Telegram-multi] Started bot for user=%s", cfg.user_id[:8])

            if scheduler is not None:
                _register_user_scheduler_jobs(scheduler, cfg, sm, bot, provider)
        except Exception as exc:
            logger.warning(
                "[Telegram-multi] Failed to start bot for user=%s: %s",
                cfg.user_id[:8], exc,
            )

    return bots


# ── Hot-add registry (so dashboard can spin up a new user without restart) ──

DEFAULT_MAX_ACTIVE_USERS = 20

# Maintained by start_all_user_bots / start_user_bot / stop_user_bot.
# Keys are user_ids, values are the running TelegramBot + the scheduler info
# we need to tear down later.
_active_bots_by_uid: dict[str, dict[str, object]] = {}


def _max_active_users() -> int:
    from .. import config as _cfg
    return int(_cfg.get_int("quota", "maxActiveUsers",
                            default=DEFAULT_MAX_ACTIVE_USERS) or DEFAULT_MAX_ACTIVE_USERS)


def active_user_count() -> int:
    return len(_active_bots_by_uid)


def is_user_active(user_id: str) -> bool:
    return user_id in _active_bots_by_uid


async def start_user_bot(
    user_id: str,
    provider: "LLMProvider",
    *,
    scheduler=None,
) -> tuple[bool, str | None]:
    """Start (or replace) one user's Telegram bot in-process.

    Called by the dashboard right after a user saves their Telegram token, so
    they don't have to wait for the next daemon restart to get their bot live.

    Returns ``(ok, error)`` — when ok is False, error is a human-readable
    reason (capacity reached, no token, etc).
    """
    # Fetch this user's settings
    settings = await get_user_settings(user_id)
    token = (settings or {}).get("telegram_bot_token") if settings else None
    if not token:
        return False, "user has no telegram bot token saved"

    raw_chat = (settings or {}).get("telegram_chat_id")
    try:
        chat_id = int(raw_chat) if raw_chat is not None else None
    except (TypeError, ValueError):
        chat_id = None

    # Soft cap — new users only; if this user is already active, we still
    # let them replace their bot below.
    if user_id not in _active_bots_by_uid and active_user_count() >= _max_active_users():
        return False, (
            f"at capacity ({active_user_count()}/{_max_active_users()} active "
            "users); contact the operator to scale up"
        )

    # If this user already has a bot running, stop it first.
    await stop_user_bot(user_id)

    cfg = UserBotConfig(user_id=user_id, bot_token=token, chat_id=chat_id)
    try:
        bot, sm = _build_tenant_bot(cfg, provider)
        await bot.start_async()
    except Exception as exc:
        logger.warning("[Telegram-multi] hot-add for user=%s failed: %s",
                       user_id[:8], exc)
        return False, f"failed to start bot: {exc}"

    if scheduler is not None:
        try:
            _register_user_scheduler_jobs(scheduler, cfg, sm, bot, provider)
        except Exception as exc:
            logger.warning("[Telegram-multi] scheduler reg failed for user=%s: %s",
                           user_id[:8], exc)

    _active_bots_by_uid[user_id] = {"bot": bot, "sm": sm, "cfg": cfg}
    logger.info("[Telegram-multi] hot-added bot for user=%s "
                "(now %d/%d active)",
                user_id[:8], active_user_count(), _max_active_users())
    return True, None


async def stop_user_bot(user_id: str) -> bool:
    """Stop and de-register one user's bot. Returns True if anything was running."""
    entry = _active_bots_by_uid.pop(user_id, None)
    if entry is None:
        return False
    bot = entry.get("bot")
    if bot is not None and hasattr(bot, "stop_async"):
        try:
            await bot.stop_async()
        except Exception as exc:
            logger.warning("[Telegram-multi] stop failed for user=%s: %s",
                           user_id[:8], exc)
    logger.info("[Telegram-multi] stopped bot for user=%s", user_id[:8])
    return True


async def stop_all_bots(bots: list[TelegramBot]) -> None:
    for bot in bots:
        try:
            await bot.stop_async()
        except Exception:
            logger.warning("[Telegram-multi] Error stopping bot", exc_info=True)
