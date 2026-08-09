"""
Probabilistic proactive messaging for ClawSoul — with Soul Mate emotional gating.

Instead of fixed time slots, a cron job fires every 5 minutes (288 ticks/day).
Each tick rolls a probability of 2/288 – 5/288 to decide whether to send a
message.  Quiet hours (default 0:00–8:00) are skipped entirely.
Daily cap (default 6) prevents over-messaging.

Soul Mate Phase 1 upgrades:
  - Sentiment-aware gating: higher probability when user seems down
  - Template differentiation based on emotional context
  - Unfinished topic follow-up
  - Long-silence detection

Proactive messages are generated in the SAME session the user chats in, via
``chat_proactive`` — the synthetic instruction ("it's morning, say something")
is dropped from the transcript but her reply is kept, so when the user answers,
the conversation actually contains what she just sent. A separate session would
leave her with no memory of her own message.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import config
from ..core import lang as _lang

if TYPE_CHECKING:
    from ..channels.telegram_bot import TelegramBot
    from ..session_manager import SessionManager

logger = logging.getLogger(__name__)

_WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_WEEKDAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

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

_TIME_HINTS_EN: dict[str, str] = {
    "early_morning": "you just woke up, still a bit groggy",
    "morning":       "you're up, having coffee or about to start work",
    "late_morning":  "morning's almost gone, you're painting or in the middle of work",
    "noon":          "lunch time",
    "afternoon":     "it's afternoon — you're working / slacking off / having tea",
    "late_afternoon":"early evening — work is mostly wrapping up",
    "evening":       "it's evening, you're relaxing and unwinding",
    "late_evening":  "getting late, you're still up",
    "night":         "very late, you're heading to bed soon",
}

# ── Soul Mate: sentiment-aware prompt templates ──────────────────────────────

_SENTIMENT_PROMPTS: dict[str, str] = {
    "negative": "对方今天心情不太好。温柔地关心一下，不用追问原因，表达陪伴。",
    "positive": "对方今天心情不错。分享开心或者问问他在开心什么。",
    "neutral": "日常随意聊聊。",
    "long_silence": "已经很久没联系了。发一条简短温馨的消息。",
    "unfinished": "上次聊到{topic}还没聊完，主动提起来。",
}

_SENTIMENT_PROMPTS_EN: dict[str, str] = {
    "negative":     "They seem a bit down today. Reach out gently — don't dig for the reason, just be present.",
    "positive":     "They seem in a good mood today. Share something light, or ask what's making them happy.",
    "neutral":      "Casual everyday check-in.",
    "long_silence":"It's been a while since you've talked. Send something short and warm.",
    "unfinished":   "Last time you were talking about {topic} and it didn't wrap up — bring it up naturally.",
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
    "分享一个你今天的小日常：手头事情的进展、宠物或室友的趣事、吃了什么好吃的、拍到好看的云等。",
    "关心一下对方的身体状况或工作状态，撒个娇。",
    "聊聊你自己今天的一个小情绪或小烦恼（跟对方无关的），像朋友一样吐个槽，让关系是双向的。",
    "就一件具体的事问问对方的看法或建议——你真的想听他怎么想。",
]

_ENRICHMENTS_EN = [
    "Check the weather where you live — if anything's worth mentioning (cold snap, rain, blazing sun), drop it in naturally.",
    "Glance at your schedule today; if there's something happening near this time, you can chat about it.",
    "Use `recall` to peek at what they said recently — if there's a thread worth following (a project / plan / mood they mentioned), bring it up.",
    "Think about what you've been watching, reading, or listening to lately — recommend one to them.",
    "Look for any fun news or trending topic and riff on it in your own words.",
    "Share a small slice of your day: how something you're working on is going, a silly thing your pet or roommate did, food you had, a pretty cloud you spotted.",
    "Check in on how they're feeling physically or how work's going — let yourself be a little needy.",
    "Vent a little about a small mood or annoyance of YOUR own (nothing to do with them) — like a partner would, so it goes both ways.",
    "Ask their opinion or advice on one specific thing — you genuinely want to hear what they think.",
]

# ── Soul Mate: sentiment gating threshold ────────────────────────────────────

# When recent sentiment is negative, multiply base probability by this factor
_NEGATIVE_SENTIMENT_BOOST = 2.5
# Hours of silence before triggering long-silence mode
_LONG_SILENCE_HOURS = 12


def _get_sentiment_context(agent: Any) -> dict[str, Any]:
    """Get the user's recent emotional context from the Soul Mate affect system.

    Returns a dict with keys: 'sentiment', 'topics', 'has_unfinished', 'summary'.
    Returns defaults if affect data is unavailable.
    """
    result: dict[str, Any] = {
        "sentiment": "neutral",
        "topics": [],
        "has_unfinished": False,
        "summary": "",
        "hours_since_last": 999,
    }
    try:
        if not hasattr(agent, "memory") or not hasattr(agent.memory, "emotional_graph"):
            return result

        # Recent emotional summary — query once, reuse
        recent_events = agent.memory.emotional_graph.get_recent(days=1)
        if recent_events:
            # Most recent event sentiment
            latest = recent_events[-1]
            result["sentiment"] = latest.get("sentiment", "neutral")
            result["summary"] = latest.get("context_summary", "")

        # Check for unfinished topics from last chat
        if recent_events:
            result["has_unfinished"] = any(
                e.get("sentiment") == "negative" and e.get("intensity", 0) > 0.6
                for e in recent_events
            )

        # Topics from timeline
        if hasattr(agent.memory, "timeline"):
            topics = agent.memory.timeline.get_topics()
            result["topics"] = topics[:5] if topics else []

        # Hours since last message (rough estimation from last timeline event)
        if hasattr(agent.memory, "timeline"):
            recent_tl = agent.memory.timeline.get_timeline(
                (datetime.now() - timedelta(days=7)).isoformat(),
                datetime.now().isoformat(),
            )
            if recent_tl:
                last_ts = recent_tl[-1].timestamp
                try:
                    last_dt = datetime.fromisoformat(last_ts)
                    delta = datetime.now() - last_dt
                    result["hours_since_last"] = delta.total_seconds() / 3600
                except (ValueError, TypeError):
                    pass

    except Exception:
        logger.debug("Failed to get sentiment context for proactive", exc_info=True)

    return result


def _get_unfinished_topics(agent: Any) -> list[str]:
    """Get topics that may need follow-up."""
    try:
        if not hasattr(agent.memory, "timeline"):
            return []
        topics = agent.memory.timeline.get_topics()
        # Prefer topics with recent negative sentiment
        return topics[:3] if topics else []
    except Exception:
        return []


def _build_wish_prompt(wish_text: str, now: datetime) -> str:
    """Prompt the agent to surface a previously recorded wish to the user.

    Used by ProactiveMessenger._maybe_send_wish when a wish's score exceeds
    threshold.  The agent should naturally bring up the topic in-persona,
    not robotically read it back.
    """
    time_str = now.strftime("%H:%M")
    if _lang.is_chinese():
        weekday = _WEEKDAYS_ZH[now.weekday()]
        return (
            f"现在是{weekday} {time_str}。\n"
            f"对方之前说过想做这件事：「{wish_text}」。\n"
            f"主动跟对方提一下，看看现在要不要做、什么时候做、或者有没有什么想法。\n"
            f"不要直接复述「你之前说过」，自然地把这事带起来，像是你也想到了一样。"
        )
    weekday = _WEEKDAYS_EN[now.weekday()]
    return (
        f"It's {weekday} {time_str}.\n"
        f"They mentioned wanting to do this: \"{wish_text}\".\n"
        "Bring it up naturally — see if they want to do it now, when, or "
        "if they have any thoughts.\n"
        "Don't say \"you mentioned\" verbatim; weave it in like you also "
        "happened to be thinking about it."
    )


def _todays_personal_dates() -> list:
    """Personal dates (their birthday, an interview…) landing today."""
    try:
        from ..core.personal_dates import PersonalDates
        return PersonalDates().today_hits()
    except Exception:
        return []


def _build_prompt(
    now: datetime,
    sentiment_context: dict[str, Any] | None = None,
    agent: Any = None,
) -> str:
    time_str = now.strftime("%H:%M")
    slot = _time_slot(now.hour)
    is_cn = _lang.is_chinese()

    # Pick 0–2 random enrichments so each message feels different
    enrichments_pool = _ENRICHMENTS if is_cn else _ENRICHMENTS_EN
    k = random.choices([0, 1, 2], weights=[3, 5, 2], k=1)[0]
    extras = random.sample(enrichments_pool, k=min(k, len(enrichments_pool)))
    extra_block = "\n".join(extras)

    # ── Soul Mate: sentiment-aware prompt section ──────────────────────────────
    sentiment_table = _SENTIMENT_PROMPTS if is_cn else _SENTIMENT_PROMPTS_EN
    sentiment_instruction = ""
    if sentiment_context:
        sentiment = sentiment_context.get("sentiment", "neutral")
        hours_since = sentiment_context.get("hours_since_last", 999)
        has_unfinished = sentiment_context.get("has_unfinished", False)
        topics = sentiment_context.get("topics", [])

        if hours_since > _LONG_SILENCE_HOURS:
            sentiment_instruction = sentiment_table["long_silence"]
        elif has_unfinished or sentiment == "negative":
            sentiment_instruction = sentiment_table.get(sentiment, sentiment_table["neutral"])
        else:
            sentiment_instruction = sentiment_table.get(sentiment, sentiment_table["neutral"])

        # Add unfinished topic reference
        if has_unfinished and topics:
            if is_cn:
                sentiment_instruction += f"\n对方可能还在想上次聊的{topics[0]}。"
            else:
                sentiment_instruction += (
                    f"\nThey might still be thinking about the {topics[0]} you "
                    "were chatting about last time."
                )

    # ── Follow up on something SPECIFIC ────────────────────────────────────
    # A topic label alone ("work stress") can't produce "did the thing with
    # your boss blow over?" — pull the actual event summary from the affect
    # graph so she can reference the real thing.
    thread = ""
    if agent is not None:
        try:
            from ..core.humanize import open_thread
            thread = open_thread(agent)
        except Exception:
            thread = ""
    if thread:
        sentiment_instruction += (
            f"\n你还记着最近这件事 —— {thread}。如果自然的话，就具体问问它"
            "（提到那件事本身，而不只是话题），要是对方当时不开心，先关心一句。"
            if is_cn else
            f"\nA recent thread you remember — {thread}. If it fits naturally, "
            "follow up on THIS specifically (reference the actual thing, not "
            "just the topic); if they seemed upset about it, lead with care."
        )

    # ── Their day comes first ──────────────────────────────────────────────
    date_hits = _todays_personal_dates()
    if date_hits:
        labels = "；".join(h.label for h in date_hits[:2])
        sentiment_instruction = (
            f"最重要的一件事：今天是 {labels}。这条消息就该是关于这件事的，"
            "用你自己的口吻（生日要真心实意地庆祝一下；面试/考试就好好加油）。"
            "别写成普通的问候。\n" + sentiment_instruction
            if is_cn else
            f"IMPORTANT — today is: {labels}. Your message should be about THIS, "
            "in your own voice (a birthday gets a real, personal celebration "
            "from a partner; an interview or exam gets a warm good-luck). Skip "
            "generic check-in content.\n" + sentiment_instruction
        )

    if is_cn:
        weekday = _WEEKDAYS_ZH[now.weekday()]
        hint = _TIME_HINTS[slot]
        parts = [
            f"现在是{weekday} {time_str}，{hint}。",
            "你想主动给男朋友发条消息来触发对话。",
        ]
        if sentiment_instruction:
            parts.append(sentiment_instruction)
        if extra_block:
            parts.append(extra_block)
        parts.append(
            "像在微信上打字，每段10-50字，最多2-3段，用空行隔开。"
            "直接发消息内容，不要有任何前缀、解释或引号。"
        )
        return "\n".join(parts)

    weekday = _WEEKDAYS_EN[now.weekday()]
    hint = _TIME_HINTS_EN[slot]
    parts = [
        f"It's {weekday} {time_str} — {hint}.",
        "You want to text your partner first to start a conversation.",
    ]
    if sentiment_instruction:
        parts.append(sentiment_instruction)
    if extra_block:
        parts.append(extra_block)
    parts.append(
        "Text like you're on iMessage: each paragraph 15–90 characters, "
        "at most 2–3 paragraphs separated by a blank line. "
        "Output the message content directly — no prefix, no explanation, "
        "no quotes."
    )
    return "\n".join(parts)


def _proactive_chat(agent, prompt: str) -> str:
    """Generate an unprompted message on the SHARED conversation.

    Uses ``chat_proactive`` when available: it drops the synthetic instruction
    ("it's morning — say something") from the transcript but keeps her reply,
    so the next thing the user says lands in a conversation that actually
    contains what she just sent. Plain ``chat`` would persist the instruction
    as if the user had typed it.
    """
    fn = getattr(agent, "chat_proactive", None)
    return fn(prompt) if callable(fn) else agent.chat(prompt)


class ProactiveMessenger:
    """Probabilistic proactive messaging via Telegram, with Soul Mate emotional gating."""

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

    def _chat_session_id(self, chat_id: int) -> str:
        """The session the USER is talking in — proactive messages belong in
        the same conversation, not a parallel one."""
        try:
            return self._telegram_bot._session_id(chat_id)  # noqa: SLF001
        except Exception:
            return f"telegram:{chat_id}"

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

            # ── Wishlist preempt: if a wish is due, surface it instead of
            #    rolling a random proactive. Wish-driven nudges consume the
            #    same maxDaily budget as normal proactive messages.
            if await self._maybe_send_wish(chat_id, now):
                return

            lo, hi = self._prob_range()
            threshold = random.uniform(lo, hi)

            # ── Soul Mate: emotional gating ──────────────────────────────────
            # Get sentiment context to adjust probability
            session_id = self._chat_session_id(chat_id)
            agent = self._sm.get(session_id)
            sentiment_ctx = _get_sentiment_context(agent) if agent else {}

            # ── Personal dates preempt ──────────────────────────────────────
            # Their birthday / an interview today is the one thing that must
            # never be lost to a dice roll. Quiet hours and the daily cap above
            # still apply, so it lands on the first eligible morning tick.
            if _todays_personal_dates():
                logger.info("[Proactive] Personal date today — bypassing the roll.")
                await self._generate_and_send(chat_id, now, sentiment_ctx)
                return

            # Boost probability if user seems down
            if sentiment_ctx.get("sentiment") == "negative":
                threshold *= _NEGATIVE_SENTIMENT_BOOST
                logger.debug(
                    "[Proactive] Negative sentiment detected — boosting threshold to %.4f",
                    threshold,
                )

            # Boost probability if long silence
            hours_since = sentiment_ctx.get("hours_since_last", 999)
            if hours_since > _LONG_SILENCE_HOURS:
                threshold *= 1.5
                logger.debug(
                    "[Proactive] Long silence (%.1f h) — boosting threshold to %.4f",
                    hours_since, threshold,
                )

            roll = random.random()
            if roll > threshold:
                return

            logger.info(
                "[Proactive] Tick hit! (roll=%.4f <= %.4f) — sending message #%d today.",
                roll, threshold, self._today_count + 1,
            )
            await self._generate_and_send(chat_id, now, sentiment_ctx)
        finally:
            self._running_tick = False

    # ── Message generation & delivery ────────────────────────────────────────

    async def _generate_and_send(
        self,
        chat_id: int,
        now: datetime,
        sentiment_ctx: dict[str, Any] | None = None,
    ) -> None:
        session_id = self._chat_session_id(chat_id)
        agent = self._sm.get_or_create(session_id)
        # Built after the agent exists so it can read the affect graph for a
        # specific follow-up.
        prompt = _build_prompt(now, sentiment_ctx, agent=agent)
        loop = asyncio.get_event_loop()

        # ── Soul Mate: update proactive count for milestones ────────────────
        try:
            if hasattr(agent, "memory") and hasattr(agent.memory, "milestones"):
                data = agent.memory.milestones.get_data()
                current_pro = data.get("total_proactive_messages", 0)
                agent.memory.milestones.check_milestones(
                    proactive_count=current_pro + 1,
                )
        except Exception:
            pass

        try:
            async with self._sm.acquire(session_id):
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, _proactive_chat, agent, prompt),
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
            return

        # Optionally attach a selfie — gated by config probability and Seedream key.
        await self._maybe_send_selfie(chat_id)

    async def _maybe_send_wish(self, chat_id: int, now: datetime) -> bool:
        """If a pending wish scores high enough, surface it and return True.

        Wish-driven nudges preempt the random proactive roll, consume one of
        the maxDaily slots, and reset the wish's last_surfaced_at so it
        doesn't fire again within the cool-down window.
        """
        try:
            from .wishlist import WishlistManager
        except ImportError:
            return False

        wl = WishlistManager()
        due = wl.due_now(now)
        if not due:
            return False
        wish, score = due[0]
        logger.info("[Proactive] Wish '%s' due (score=%.3f)", wish.text[:40], score)

        prompt = _build_wish_prompt(wish.text, now)
        session_id = self._chat_session_id(chat_id)
        agent = self._sm.get_or_create(session_id)
        loop = asyncio.get_event_loop()
        try:
            async with self._sm.acquire(session_id):
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, _proactive_chat, agent, prompt),
                    timeout=300.0,
                )
        except Exception as exc:
            logger.exception("[Proactive] Wish generation failed: %s", exc)
            return False

        text = (response or "").strip()
        if not text:
            return False

        try:
            await self._telegram_bot.send_message(chat_id, text)
            self._today_count += 1
            wl.mark_surfaced(wish.id)
            logger.info(
                "[Proactive] Surfaced wish #%d to chat %s (id=%s)",
                self._today_count, chat_id, wish.id,
            )
            return True
        except Exception as exc:
            logger.error("[Proactive] Wish delivery failed: %s", exc)
            return False

    async def _maybe_send_selfie(self, chat_id: int) -> None:
        """Roll the dice on attaching a selfie after a proactive message."""
        try:
            from ..core.image_gen import take_selfie
            from ..core.image_gen.selfie import is_enabled
        except ImportError:
            return
        if not is_enabled():
            return
        probability = float(config.get("selfie", "proactiveProbability", default=0.15) or 0)
        if probability <= 0 or random.random() > probability:
            return

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, take_selfie)
            await self._telegram_bot.send_photo(
                chat_id, result.path, caption=result.caption(),
            )
            logger.info("[Proactive] attached selfie %s", result.path)
        except Exception as exc:
            logger.warning("[Proactive] selfie attach failed: %s", exc)

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
