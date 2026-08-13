"""
SelfieScheduler — fires N selfies per day at configured times.

Configuration (herandhim.json):

    "selfie": {
        "enabled": true,
        "schedule": ["10:00", "16:00", "20:00"],   // local-time cron points
        "chatId": 123456789,                       // Telegram chat to deliver to
        "model": null,                             // override Seedream model
        "maxDaily": 3                              // safety cap
    }
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import config
from ..core.image_gen import take_selfie
from ..core.image_gen.selfie import is_enabled

if TYPE_CHECKING:
    from ..channels.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


DEFAULT_SCHEDULE = ["10:00", "16:00", "20:00"]


class SelfieScheduler:
    """Schedules and dispatches automatic selfies through APScheduler."""

    def __init__(
        self,
        telegram_bot: "TelegramBot | None" = None,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        self._tg = telegram_bot
        self._scheduler = scheduler or AsyncIOScheduler(timezone="UTC")
        self._owned_scheduler = scheduler is None
        self._sent_today = 0
        self._last_date: str | None = None

    # ── Schedule wiring ─────────────────────────────────────────────────

    def _read_schedule(self) -> list[str]:
        raw = config.get_list("selfie", "schedule") or DEFAULT_SCHEDULE
        cleaned: list[str] = []
        for t in raw:
            if isinstance(t, str) and ":" in t:
                hh, _, mm = t.partition(":")
                if hh.isdigit() and mm.isdigit():
                    cleaned.append(f"{int(hh):02d}:{int(mm):02d}")
        return cleaned or DEFAULT_SCHEDULE

    def register(self) -> int:
        if not is_enabled():
            logger.info("[SelfieScheduler] disabled (no Seedream key or selfie.enabled=false)")
            return 0

        times = self._read_schedule()
        for slot in times:
            hh, mm = slot.split(":")
            self._scheduler.add_job(
                self._fire,
                trigger=CronTrigger(hour=int(hh), minute=int(mm)),
                id=f"selfie_{slot}",
                replace_existing=True,
            )
        logger.info("[SelfieScheduler] registered %d slot(s): %s", len(times), times)
        return len(times)

    def set_telegram_bot(self, tg: "TelegramBot | None") -> None:
        self._tg = tg

    def start(self) -> None:
        if self._owned_scheduler and not self._scheduler.running:
            self._scheduler.start()

    def stop(self) -> None:
        if self._owned_scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # ── Firing ──────────────────────────────────────────────────────────

    def _daily_cap_reached(self) -> bool:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_date != today:
            self._last_date = today
            self._sent_today = 0
        cap = config.get_int("selfie", "maxDaily", default=len(DEFAULT_SCHEDULE))
        return self._sent_today >= cap

    async def _fire(self) -> None:
        if self._daily_cap_reached():
            logger.info("[SelfieScheduler] daily cap reached, skipping")
            return

        chat_id = config.get_int("selfie", "chatId")
        if not chat_id:
            chat_id = config.get_int("proactive", "chatId")
        if not chat_id:
            logger.warning("[SelfieScheduler] no chatId configured, skipping")
            return

        model = config.get_str("selfie", "model", default="") or None

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: take_selfie(model=model),
            )
        except Exception as exc:
            logger.exception("[SelfieScheduler] generation failed: %s", exc)
            return

        if not self._tg:
            logger.info("[SelfieScheduler] generated %s but no Telegram bot wired", result.path)
            return

        try:
            await self._tg.send_photo(chat_id, result.path, caption=result.caption())
            self._sent_today += 1
            logger.info(
                "[SelfieScheduler] sent selfie #%d to chat %d (%s)",
                self._sent_today, chat_id, result.path,
            )
        except Exception as exc:
            logger.error("[SelfieScheduler] Telegram send failed: %s", exc)
