"""
Daemon server for ClawSoul — Telegram channel.

Starts the Telegram bot alongside the web dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time as _time

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


async def start_telegram(
    provider: LLMProvider,
    fastapi_app=None,
) -> list:
    """Start the Telegram bot as a background task.

    Parameters
    ----------
    provider    : LLM provider instance
    fastapi_app : optional FastAPI app instance (unused, kept for interface compat)

    Returns the list of successfully started bot objects.
    """
    store = SessionStore()
    session_manager = SessionManager(agent_factory=lambda sid: None, store=store)

    # Detect local timezone for cron scheduling
    _tz = _detect_local_timezone()
    logger.info("[ClawSoul] Using timezone: %s", _tz)

    scheduler = CronScheduler(
        session_manager=session_manager,
        timezone=_tz,
    )

    def agent_factory(session_id: str) -> PersistentAgent:
        return PersistentAgent(
            provider=provider,
            store=store,
            session_id=session_id,
            cron_manager=scheduler,
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

    # ── 6. Start heartbeat monitor ───────────────────────────────────────────
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


async def run_server(
    provider: LLMProvider,
) -> None:
    """Standalone server entry point (Telegram only, no web).

    Kept for backward compatibility. Prefer using ``start_telegram``
    together with the web dashboard in ``_run_foreground``.
    """
    active_bots = await start_telegram(provider)

    if not active_bots:
        logger.error("[ClawSoul] Telegram bot not started. Exiting.")
        return

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("[ClawSoul] Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, OSError):
            pass

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("[ClawSoul] Shutting down...")
        for bot in active_bots:
            if hasattr(bot, 'stop_async'):
                await bot.stop_async()
        if 'scheduler' in locals():
            scheduler.stop()
        if 'hb' in locals():
            await hb.stop()
        logger.info("[ClawSoul] Shutdown complete.")
