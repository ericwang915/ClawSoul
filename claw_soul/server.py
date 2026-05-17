"""
Daemon server for ClawSoul — Telegram channel.

Starts the Telegram bot alongside the web dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from .core.llm.base import LLMProvider
from .core.persistent_agent import PersistentAgent
from .core.session_store import SessionStore
from .scheduler.cron import CronScheduler
from .scheduler.heartbeat import create_heartbeat
from .scheduler.planner import generate_daily_plan, plan_is_stale, register_daily_planner
from .scheduler.proactive import ProactiveMessenger, get_proactive_chat_id
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


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

    scheduler = CronScheduler(
        session_manager=session_manager,
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

    try:
        from .channels.telegram_bot import create_bot_from_env
        bot = create_bot_from_env(session_manager)
        scheduler._telegram_bot = bot
        await bot.start_async()
        active_bots.append(bot)
        logger.info("[ClawSoul] Telegram bot started.")

        # Register Daily Planner
        register_daily_planner(scheduler._scheduler, provider)

        # Generate today's plan if missing or stale (from a previous day)
        from . import config as _cfg
        if plan_is_stale():
            logger.info("[ClawSoul] Plan is stale or missing — generating now.")
            asyncio.create_task(generate_daily_plan(provider))

        # Register Proactive Messaging
        proactive_enabled = _cfg.get_bool("proactive", "enabled", default=True)
        proactive_chat_id = get_proactive_chat_id()
        if proactive_enabled and proactive_chat_id:
            proactive = ProactiveMessenger(
                session_manager=session_manager,
                telegram_bot=bot,
            )
            proactive.register(scheduler._scheduler, proactive_chat_id)
        elif proactive_enabled:
            logger.warning("[ClawSoul] Proactive messaging enabled but no chat_id found. Set proactive.chatId or channels.telegram.allowedUsers.")

    except Exception as exc:
        logger.warning("[ClawSoul] Telegram failed to start: %s", exc)

    if active_bots:
        scheduler.start()

        # Start heartbeat monitor
        try:
            hb = create_heartbeat(provider, telegram_bot=active_bots[0] if active_bots else None)
            await hb.start()
        except Exception as exc:
            logger.warning("[ClawSoul] Heartbeat monitor failed to start: %s", exc)
    else:
        logger.warning("[ClawSoul] Telegram bot not started — check token in claw_soul.json.")

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
        logger.info("[ClawSoul] Shutdown complete.")
