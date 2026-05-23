"""
Probabilistic proactive messaging for ClawSoul.

Instead of fixed time slots, a cron job fires every 5 minutes (288 ticks/day).
Each tick rolls a probability of 2/288 – 5/288 to decide whether to send a
message.  Quiet hours (default 0:00–8:00) are skipped entirely.
Daily cap (default 6) prevents over-messaging.

The single session "proactive:main" gives the agent continuity across all
proactive messages, while shared Memory lets it reference past user
conversations.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import config

if TYPE_CHECKING:
    from ..channels.telegram_bot import TelegramBot
    from ..session_manager import SessionManager

logger = logging.getLogger(__name__)

_WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_TIME_HINTS: dict[str, str] = {
    "early_morning": "你刚起床还有点迷糊",
    "morning":       "你起来了，在喝咖啡或者准备开始工作",
    "late_morning":  "上午快过去了，你在画画或者工作中",
    "noon":          "中午饭点了",
    "afternoon":     "下午了，你在工作/摸鱼/喝下午茶",
    "late_afternoon":"快傍晚了，今天的工作差不多了",
    "evening":       "晚上了，你在放松休息",
    "late_evening":  "夜深了，你还在熬夜",
    "night":         "很晚了，你准备去睡觉",
}


def _time_slot(hour: int) -> str:
    if hour < 9:
        return "early_morning"
    if hour < 10:
        return "morning"
    if hour < 11:
        return "late_morning"
    if hour < 13:
        return "noon"
    if hour < 16:
        return "afternoon"
    if hour < 18:
        return "late_afternoon"
    if hour < 21:
        return "evening"
    if hour < 23:
        return "late_evening"
    return "night"


# Contextual enrichments the prompt may randomly include (0–2 per message).
# Each is a hint that the agent *may* weave into its message, not a mandate.
_ENRICHMENTS = [
    "查一下你所在城市现在的天气，如果有值得提的（降温、下雨、大太阳等），自然地提一嘴。",
    "看看你今天的日程安排，如果现在这个时间点附近有什么活动，可以顺便聊聊。",
    "用 recall 看看对方最近聊过什么，如果有值得跟进的话题（比如对方提到的项目/计划/心情），可以主动问问后续。",
    "想想你最近在看/听什么（动漫、歌、书、电影），挑一个安利给对方。",
    "看看有没有什么有趣的新闻或热搜，用自己的话随口聊聊。",
    "分享一个你今天的小日常：画画进度、芝麻趣事、吃了什么好吃的、拍到好看的云等。",
    "关心一下对方的身体状况或工作状态，撒个娇。",
]


def _build_prompt(now: datetime) -> str:
    time_str = now.strftime("%H:%M")
    weekday = _WEEKDAYS_ZH[now.weekday()]
    slot = _time_slot(now.hour)
    hint = _TIME_HINTS[slot]

    # Pick 0–2 random enrichments so each message feels different
    k = random.choices([0, 1, 2], weights=[3, 5, 2], k=1)[0]
    extras = random.sample(_ENRICHMENTS, k=min(k, len(_ENRICHMENTS)))
    extra_block = "\n".join(extras)

    parts = [
        f"现在是{weekday} {time_str}，{hint}。",
        "你想主动给男朋友发条消息来触发对话。",
    ]
    if extra_block:
        parts.append(extra_block)
    parts.append(
        "像在微信上打字，每段10-50字，最多2-3段，用空行隔开。"
        "直接发消息内容，不要有任何前缀、解释或引号。"
    )
    return "\n".join(parts)


class ProactiveMessenger:
    """Probabilistic proactive messaging via Telegram."""

    def __init__(
        self,
        session_manager: "SessionManager",
        telegram_bot: "TelegramBot",
    ) -> None:
        self._sm = session_manager
        self._telegram_bot = telegram_bot
        self._today: date | None = None
        self._today_count: int = 0
        self._running_tick: bool = False

    # ── Configuration helpers ────────────────────────────────────────────────

    @staticmethod
    def _max_daily() -> int:
        return config.get_int("proactive", "maxDaily", default=6)

    @staticmethod
    def _quiet_range() -> tuple[int, int]:
        """Return (start_hour, end_hour) for quiet hours (no messages)."""
        start = config.get_int("proactive", "quietStart", default=0)
        end = config.get_int("proactive", "quietEnd", default=8)
        return start, end

    @staticmethod
    def _prob_range() -> tuple[float, float]:
        lo = config.get("proactive", "probMin", default=2 / 288)
        hi = config.get("proactive", "probMax", default=5 / 288)
        return float(lo), float(hi)

    # ── Tick logic ───────────────────────────────────────────────────────────

    def _reset_if_new_day(self, today: date) -> None:
        if self._today != today:
            self._today = today
            self._today_count = 0

    def _in_quiet_hours(self, hour: int) -> bool:
        start, end = self._quiet_range()
        if start <= end:
            return start <= hour < end
        # Wrapping range (e.g. 23 – 7)
        return hour >= start or hour < end

    async def _tick(self, chat_id: int) -> None:
        """Called every 5 minutes by APScheduler."""
        if self._running_tick:
            logger.debug("[Proactive] Previous tick still running — skipping.")
            return
        self._running_tick = True
        try:
            now = datetime.now()
            self._reset_if_new_day(now.date())

            if self._today_count >= self._max_daily():
                return

            if self._in_quiet_hours(now.hour):
                return

            lo, hi = self._prob_range()
            threshold = random.uniform(lo, hi)
            roll = random.random()
            if roll > threshold:
                return

            logger.info(
                "[Proactive] Tick hit! (roll=%.4f <= %.4f) — sending message #%d today.",
                roll, threshold, self._today_count + 1,
            )
            await self._generate_and_send(chat_id, now)
        finally:
            self._running_tick = False

    # ── Message generation & delivery ────────────────────────────────────────

    async def _generate_and_send(self, chat_id: int, now: datetime) -> None:
        prompt = _build_prompt(now)
        session_id = "proactive:main"
        agent = self._sm.get_or_create(session_id)
        loop = asyncio.get_event_loop()

        try:
            async with self._sm.acquire(session_id):
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, agent.chat, prompt),
                    timeout=300.0,
                )
        except Exception as exc:
            logger.exception("[Proactive] Generation failed: %s", exc)
            return

        text = (response or "").strip()
        if not text:
            return

        try:
            await self._telegram_bot.send_message(chat_id, text)
            self._today_count += 1
            logger.info(
                "[Proactive] Delivered message #%d to chat %s.",
                self._today_count, chat_id,
            )
        except Exception as exc:
            logger.error("[Proactive] Telegram delivery failed: %s", exc)

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, scheduler: AsyncIOScheduler, chat_id: int) -> None:
        """Register the every-5-minute tick job."""
        scheduler.add_job(
            self._tick,
            trigger=CronTrigger(minute="*/5"),
            id="proactive_tick",
            kwargs={"chat_id": chat_id},
            replace_existing=True,
        )
        lo, hi = self._prob_range()
        q_start, q_end = self._quiet_range()
        logger.info(
            "[Proactive] Registered (every 5 min, prob=%.4f–%.4f, "
            "quiet=%02d:00–%02d:00, max=%d/day).",
            lo, hi, q_start, q_end, self._max_daily(),
        )


def get_proactive_chat_id() -> int | None:
    """Determine which Telegram chat_id to send proactive messages to.

    Priority:
      1. proactive.chatId in config
      2. First entry in channels.telegram.allowedUsers
    """
    explicit = config.get_int("proactive", "chatId", default=0)
    if explicit:
        return explicit

    allowed = config.get_int_list("channels", "telegram", "allowedUsers")
    if allowed:
        return allowed[0]

    return None
