"""
Daemon server for ClawSoul — Telegram channel.

Starts the Telegram bot alongside the web dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time

from . import config
from .core.llm.base import LLMProvider
from .core.persistent_agent import PersistentAgent
from .core.session_store import SessionStore
from .scheduler.cron import CronScheduler
from .scheduler.heartbeat import create_heartbeat
from .scheduler.planner import generate_daily_plan, plan_is_stale, register_daily_planner
from .scheduler.proactive import ProactiveMessenger, get_proactive_chat_id
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


def _detect_local_timezone() -> str:
    """Detect the local IANA timezone for cron scheduling.

    Check order:
    1. TZ environment variable
    2. /etc/localtime symlink (Linux/macOS)
    3. time.timezone offset → Etc/GMT±N
    """
    # 1. Explicit TZ env var
    tz = os.environ.get("TZ")
    if tz:
        return tz

    # 2. /etc/localtime symlink (Linux/macOS)
    try:
        if os.path.islink("/etc/localtime"):
            link = os.readlink("/etc/localtime")
            # Usually: /usr/share/zoneinfo/Asia/Shanghai
            parts = link.split("/")
            # Take the last two parts (region/city)
            if len(parts) >= 2:
                candidate = "/".join(parts[-2:])
                if candidate not in ("zoneinfo",):
                    return candidate
    except Exception:
        pass

    # 3. Fallback: offset-based Etc/GMT
    # time.timezone is seconds west of UTC (negative for east)
    offset_hours = -_time.timezone // 3600
    if offset_hours == 0:
        return "UTC"
    return f"Etc/GMT{'-' if offset_hours > 0 else '+'}{abs(offset_hours)}"


def _multi_tenant_mode() -> bool:
    """Multi-tenant mode is on whenever the dashboard auth gate is on."""
    return bool(os.environ.get("SUPABASE_JWT_SECRET"))


# Module-level handles so the dashboard can reach the global APScheduler when
# hot-adding a freshly-onboarded user without restarting the daemon.
_global_scheduler = None
_active_provider = None


def get_global_scheduler():
    return _global_scheduler


def get_active_provider():
    return _active_provider


async def start_telegram(
    provider: LLMProvider,
    fastapi_app=None,
) -> list:
    """Start the Telegram bot(s) as background tasks.

    Kill switch: when ``CLAW_BOTS_DISABLED=1`` we skip Telegram entirely.
    Used by the legacy ``clawsoul`` Fly app once SaaS Phase 2 routes
    Telegram traffic through ``clawsoul-router`` — the legacy still
    serves the dashboard / Google OAuth, but starting bots here would
    have PTB call deleteWebhook and steal traffic from the router.

    In **multi-tenant mode** (``SUPABASE_JWT_SECRET`` set): launches one bot
    per Supabase user that has a saved ``telegram_bot_token``. The cron
    scheduler / proactive / selfie features are skipped — they're not yet
    per-tenant-aware. Multi-tenant scheduling lands in a later phase.

    In **single-tenant mode** (laptop / Eric's existing deploy): unchanged —
    one bot, full scheduler / proactive / heartbeat / selfies.
    """
    if os.environ.get("CLAW_BOTS_DISABLED", "").strip() in ("1", "true", "yes"):
        logger.info("[ClawSoul] CLAW_BOTS_DISABLED — skipping Telegram bot startup")
        return []

    global _global_scheduler, _active_provider
    _active_provider = provider

    if _multi_tenant_mode():
        logger.info("[ClawSoul] Multi-tenant mode — starting per-user Telegram bots + scheduler")
        from .channels import telegram_multi

        # One global APScheduler shared across all users. Each user's daily
        # planner / proactive / selfie jobs are registered with it, wrapped in
        # tenancy.user_context() so they read & write only their own data.
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _tz = _detect_local_timezone()
        _global_scheduler = AsyncIOScheduler(timezone=_tz)

        bots = await telegram_multi.start_all_user_bots(
            provider, scheduler=_global_scheduler,
        )

        if not _global_scheduler.running:
            _global_scheduler.start()
            logger.info(
                "[ClawSoul] Global scheduler started (%s) — %d job(s) across %d user(s)",
                _tz, len(_global_scheduler.get_jobs()), len(bots),
            )

        return bots

    store = SessionStore()
    session_manager = SessionManager(agent_factory=lambda sid: None, store=store)

    # Detect local timezone for cron scheduling
    _tz = _detect_local_timezone()
    logger.info("[ClawSoul] Using timezone: %s", _tz)

    scheduler = CronScheduler(
        session_manager=session_manager,
        timezone=_tz,
    )

    # Create a shared KnowledgeRAG singleton for all agents
    _shared_rag = None
    _knowledge_path = os.path.join(str(config.CLAWSOUL_HOME), "context", "knowledge")
    if os.path.exists(_knowledge_path):
        try:
            from .core.knowledge.rag import KnowledgeRAG
            _shared_rag = KnowledgeRAG(
                knowledge_dir=_knowledge_path,
                provider=provider,
                use_reranker=True,
            )
            logger.info("[ClawSoul] Shared KnowledgeRAG created (%d chunks)", len(_shared_rag))
        except Exception as exc:
            logger.warning("[ClawSoul] Failed to create shared KnowledgeRAG: %s", exc)

    def agent_factory(session_id: str) -> PersistentAgent:
        return PersistentAgent(
            provider=provider,
            store=store,
            session_id=session_id,
            cron_manager=scheduler,
            rag=_shared_rag,
            verbose=False,
        )

    session_manager.set_factory(agent_factory)

    active_bots: list = []

    # ── 1. Start Telegram bot (best-effort) ──────────────────────────────────
    try:
        from .channels.telegram_bot import create_bot_from_env
        bot = create_bot_from_env(session_manager)
        scheduler.set_telegram_bot(bot)
        await bot.start_async()
        active_bots.append(bot)
        logger.info("[ClawSoul] Telegram bot started.")
    except Exception as exc:
        logger.warning("[ClawSoul] Telegram bot failed to start: %s", exc)

    # ── 2. Register Daily Planner (always, regardless of Telegram status) ────
    register_daily_planner(scheduler._scheduler, provider)

    # ── 3. Generate today's plan immediately if stale ───────────────────────
    if plan_is_stale():
        logger.info("[ClawSoul] Plan is stale or missing — generating now.")
        asyncio.create_task(generate_daily_plan(provider))

    # ── 4. Start the scheduler ──────────────────────────────────────────────
    scheduler.start()

    # ── 5. Register Proactive Messaging (only if Telegram available) ────────
    try:
        from . import config as _cfg
        proactive_enabled = _cfg.get_bool("proactive", "enabled", default=True)
        proactive_chat_id = get_proactive_chat_id()
        if proactive_enabled and proactive_chat_id and active_bots:
            proactive = ProactiveMessenger(
                session_manager=session_manager,
                telegram_bot=active_bots[0],
            )
            proactive.register(scheduler._scheduler, proactive_chat_id)
        elif proactive_enabled and not proactive_chat_id:
            logger.warning("[ClawSoul] Proactive messaging enabled but no chat_id found. Set proactive.chatId or channels.telegram.allowedUsers.")
    except Exception as exc:
        logger.warning("[ClawSoul] Proactive messaging setup failed: %s", exc)

    # ── 6. Register scheduled selfies (only if Seedream + Telegram available) ─
    if active_bots:
        try:
            from .scheduler.selfie_task import SelfieScheduler
            selfie_sched = SelfieScheduler(
                telegram_bot=active_bots[0],
                scheduler=scheduler._scheduler,
            )
            registered = selfie_sched.register()
            if registered:
                logger.info("[ClawSoul] Selfie scheduler registered (%d slot(s))", registered)
        except Exception as exc:
            logger.warning("[ClawSoul] Selfie scheduler setup failed: %s", exc)

    # ── 7. Start heartbeat monitor ───────────────────────────────────────────
    if active_bots:
        try:
            hb = create_heartbeat(provider, telegram_bot=active_bots[0])
            await hb.start()
        except Exception as exc:
            logger.warning("[ClawSoul] Heartbeat monitor failed to start: %s", exc)
    else:
        logger.info("[ClawSoul] No Telegram bot active — heartbeat skipped.")

    return active_bots


async def start_channels(
    provider: LLMProvider,
    channels: list[str],
    fastapi_app=None,
) -> list:
    """Start the requested messaging channels.

    Called from the web dashboard when channels need to be (re)started.
    Currently only Telegram is supported.
    """
    bots: list = []
    if "telegram" in channels:
        bots = await start_telegram(provider, fastapi_app=fastapi_app)
    return bots
