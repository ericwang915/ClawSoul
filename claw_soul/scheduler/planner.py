"""
Daily planner for ClawSoul.

Generates a realistic 24-hour schedule once per day based on:
  - Soul & Persona (character traits, speaking style)
  - Profile (location, job, daily habits, hobbies)
  - Weather (fetched from Open-Meteo for the character's city)
  - Date context (weekday/weekend, season)
  - Random mood/energy variation

The plan is saved to ``context/calendar/today_plan.md`` and loaded into the
agent's system prompt via ``calendar_instruction``, so it naturally influences
conversations throughout the day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import config
from ..core.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# ── Random daily mood pool ───────────────────────────────────────────────────

_MOODS = [
    "今天精力充沛，心情很好，想搞点事情",
    "今天状态一般般，有点懒散，想躺平",
    "今天灵感爆棚，特别想画画",
    "今天有点累，想多休息，慢慢来",
    "今天心情特别好，想出门逛逛走走",
    "今天有点小焦虑，有个甲方催稿了",
    "今天很放松，没什么压力，随心所欲",
    "今天有点想家，想给爸妈打个电话",
    "今天很有干劲，想把拖延的事情做完",
    "今天有点丧，需要奶茶续命",
    "今天莫名开心，想给男朋友分享一堆东西",
    "今天有点感冒的迹象，嗓子不太舒服",
]

_WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# ── Holiday / special day calendar ───────────────────────────────────────────

# Fixed-date holidays & special days
_FIXED_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1):   "元旦🎉",
    (2, 14):  "情人节💕",
    (3, 8):   "妇女节/女生节",
    (3, 12):  "植树节",
    (3, 14):  "白色情人节",
    (4, 1):   "愚人节",
    (5, 1):   "劳动节🎉",
    (5, 4):   "青年节",
    (5, 20):  "520表白日💕",
    (6, 1):   "儿童节",
    (6, 18):  "618购物节🛒",
    (7, 7):   "七夕情人节💕",
    (8, 1):   "建军节",
    (9, 10):  "教师节",
    (10, 1):  "国庆节🇨🇳",
    (10, 2):  "国庆假期",
    (10, 3):  "国庆假期",
    (10, 4):  "国庆假期",
    (10, 5):  "国庆假期",
    (10, 6):  "国庆假期",
    (10, 7):  "国庆假期",
    (10, 31): "万圣节🎃",
    (11, 11): "双十一/光棍节🛒",
    (12, 24): "平安夜🎄",
    (12, 25): "圣诞节🎄",
    (12, 31): "跨年夜🎆",
}

# Lunar-calendar holidays — approximate dates, update yearly.
# Keyed by year -> list of (month, day, name).
_LUNAR_HOLIDAYS: dict[int, list[tuple[int, int, str]]] = {
    2025: [
        (1, 28, "除夕"), (1, 29, "春节🧧"), (1, 30, "春节假期"), (1, 31, "春节假期"),
        (2, 1, "春节假期"), (2, 2, "春节假期"), (2, 3, "春节假期"), (2, 4, "春节假期"),
        (2, 12, "元宵节🏮"),
        (4, 4, "清明节"),
        (5, 31, "端午节"),
        (10, 6, "中秋节🥮"),
    ],
    2026: [
        (2, 16, "除夕"), (2, 17, "春节🧧"), (2, 18, "春节假期"), (2, 19, "春节假期"),
        (2, 20, "春节假期"), (2, 21, "春节假期"), (2, 22, "春节假期"),
        (3, 3, "元宵节🏮"),
        (4, 5, "清明节"),
        (6, 19, "端午节"),
        (9, 25, "中秋节🥮"),
    ],
    2027: [
        (2, 5, "除夕"), (2, 6, "春节🧧"), (2, 7, "春节假期"), (2, 8, "春节假期"),
        (2, 9, "春节假期"), (2, 10, "春节假期"), (2, 11, "春节假期"),
        (2, 20, "元宵节🏮"),
        (4, 5, "清明节"),
        (6, 9, "端午节"),
        (9, 15, "中秋节🥮"),
    ],
}


def _get_holiday_info(now: datetime) -> str:
    """Return a description of today's holiday status and upcoming events."""
    today_key = (now.month, now.day)
    year = now.year

    # Check fixed-date holidays
    holiday = _FIXED_HOLIDAYS.get(today_key)

    # Check lunar holidays for this year
    if not holiday:
        for m, d, name in _LUNAR_HOLIDAYS.get(year, []):
            if m == now.month and d == now.day:
                holiday = name
                break

    # Check upcoming holidays (next 3 days)
    upcoming: list[str] = []
    for delta in range(1, 4):
        future = now + timedelta(days=delta)
        fkey = (future.month, future.day)
        h = _FIXED_HOLIDAYS.get(fkey)
        if not h:
            for m, d, name in _LUNAR_HOLIDAYS.get(year, []):
                if m == future.month and d == future.day:
                    h = name
                    break
        if h:
            upcoming.append(f"{delta}天后是{h}")

    parts: list[str] = []
    if holiday:
        parts.append(f"🎉 今天是{holiday}！放假/特殊日子")
    else:
        is_weekend = now.weekday() >= 5
        parts.append("周末休息日" if is_weekend else "普通工作日")

    if upcoming:
        parts.append("（" + "；".join(upcoming) + "）")

    return "".join(parts)

# ── Shanghai default coords (from PROFILE.md) ───────────────────────────────

_DEFAULT_LAT, _DEFAULT_LON = 31.2304, 121.4737

_WMO_ZH = {
    0: "晴天", 1: "大部晴", 2: "多云", 3: "阴天",
    45: "雾", 48: "霜雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    77: "雪粒", 80: "阵雨", 81: "中阵雨", 82: "大阵雨",
    85: "小雪阵", 86: "大雪阵",
    95: "雷暴", 96: "雷暴+小冰雹", 99: "雷暴+大冰雹",
}


def _fetch_weather_brief(
    lat: float = _DEFAULT_LAT,
    lon: float = _DEFAULT_LON,
) -> str:
    """Fetch today's weather summary from Open-Meteo (free, no key)."""
    try:
        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum",
            "current": "temperature_2m,weather_code",
            "forecast_days": 1,
            "timezone": "auto",
        })
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "ClawSoul/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        current = data.get("current", {})
        daily = data.get("daily", {})

        temp_now = current.get("temperature_2m", "?")
        code_now = current.get("weather_code", 0)
        hi = daily.get("temperature_2m_max", [None])[0]
        lo = daily.get("temperature_2m_min", [None])[0]
        precip = daily.get("precipitation_sum", [0])[0]

        condition = _WMO_ZH.get(code_now, f"天气代码{code_now}")

        parts = [f"当前天气：{condition}，{temp_now}°C"]
        if hi is not None and lo is not None:
            parts.append(f"今日气温：{lo}°C ~ {hi}°C")
        if precip and precip > 0:
            parts.append(f"降水量：{precip}mm")

        return "\n".join(parts)
    except Exception as exc:
        logger.warning("[Planner] Weather fetch failed: %s", exc)
        return "天气信息暂不可用"


# ── File helpers ─────────────────────────────────────────────────────────────

def _load_text(path: str) -> str:
    """Load text from a file or all files in a directory."""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    if os.path.isdir(path):
        parts = []
        for fname in sorted(os.listdir(path)):
            fpath = os.path.join(path, fname)
            if os.path.isfile(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    parts.append(f.read().strip())
        return "\n\n".join(parts)
    return ""


def _plan_path() -> str:
    return os.path.join(str(config.CLAWSOUL_HOME), "context", "calendar", "today_plan.md")


def plan_is_stale() -> bool:
    """Return True if today_plan.md is missing or was generated on a past day."""
    path = _plan_path()
    if not os.path.exists(path):
        return True
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    return mtime.date() != date.today()


def _season(month: int) -> str:
    if month in (3, 4, 5):
        return "春天"
    if month in (6, 7, 8):
        return "夏天"
    if month in (9, 10, 11):
        return "秋天"
    return "冬天"


# ── Core generation ──────────────────────────────────────────────────────────

async def generate_daily_plan(provider: LLMProvider) -> None:
    """Generate the daily plan and save it to context/calendar/today_plan.md."""
    logger.info("[Planner] Starting daily schedule generation...")

    home = str(config.CLAWSOUL_HOME)
    context_dir = os.path.join(home, "context")
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Load identity layers (user-customised, then fall back to templates)
    profile = _load_text(os.path.join(context_dir, "profile"))
    if not profile:
        profile = _load_text(os.path.join(pkg_dir, "templates", "profile", "PROFILE.md"))

    persona = _load_text(os.path.join(context_dir, "persona"))
    if not persona:
        persona = _load_text(os.path.join(pkg_dir, "templates", "persona", "girlfriend.md"))

    soul = _load_text(os.path.join(context_dir, "soul"))
    if not soul:
        soul = _load_text(os.path.join(pkg_dir, "templates", "soul", "SOUL.md"))

    # Fetch weather
    loop = asyncio.get_running_loop()
    weather = await loop.run_in_executor(None, _fetch_weather_brief)

    # Random daily mood
    mood = random.choice(_MOODS)

    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    weekday = _WEEKDAYS_ZH[now.weekday()]
    season = _season(now.month)
    holiday_info = _get_holiday_info(now)

    prompt = f"""你需要为自己规划今天一整天的日程。你就是下面这个角色，请完全代入。

## 你的核心性格
{soul[:600]}

## 你的人设
{persona[:500]}

## 你的生活背景
{profile}

## 今天的信息
- 日期：{date_str}（{weekday}）
- 季节：{season}
- 节假日：{holiday_info}
- {weather}
- 今天的精神状态：{mood}

## 要求

以第一人称为自己规划今天的日程，从起床到睡觉，精确到小时。

规则：
1. 完全基于你的角色身份来规划：你的职业、作息习惯、爱好、所在城市
2. 根据今天的天气安排室内/室外活动
3. 根据今天的精神状态调整安排松紧度
4. **如果今天是节假日或特殊日子，日程要体现出来**（放假睡懒觉、节日氛围、和男朋友/朋友庆祝等）
5. **如果快到某个节日了，可以体现期待感**（比如准备礼物、计划活动等）
6. 如果是普通周末，安排更休闲；工作日要有工作内容
7. 包含：起床、工作（画画/接稿/改稿）、吃饭、休闲（追番/看书/听歌）、和猫互动、可能的社交、运动（偶尔）、睡觉
8. 要有生活感和随机性，不要太完美，偶尔可以摸鱼、发呆、刷手机
9. 偶尔安排特别事件（和Sarah约饭、线上和阿凯聊画技、去健身房、逛展、收快递、拆盲盒等，不需要每样都有）
10. 用中文，语气随意自然

直接输出日程列表，不要输出标题或其他多余内容。格式：
- 09:30 赖在床上刷手机，芝麻趴在旁边打呼噜
- 10:00 终于爬起来，给芝麻添粮，冲了杯冰美式
- ...
"""

    try:
        messages = [{"role": "user", "content": prompt}]
        response = await loop.run_in_executor(None, provider.chat, messages, [])
        content = (response.choices[0].message.content or "").strip()

        plan_file = _plan_path()
        os.makedirs(os.path.dirname(plan_file), exist_ok=True)

        header = (
            f"# {date_str}（{weekday}）日程\n\n"
            f"> {holiday_info}\n"
            f"> {weather}\n"
            f"> 精神状态：{mood}\n\n"
        )
        with open(plan_file, "w", encoding="utf-8") as f:
            f.write(header + content)

        logger.info("[Planner] Successfully generated today's schedule.")
    except Exception as exc:
        logger.error("[Planner] Failed to generate daily plan: %s", exc)


# ── Scheduler registration ───────────────────────────────────────────────────

def register_daily_planner(
    scheduler: AsyncIOScheduler,
    provider: LLMProvider,
) -> None:
    """Register the daily planner cron job (runs at 00:01)."""
    trigger = CronTrigger(hour=0, minute=1)
    scheduler.add_job(
        generate_daily_plan,
        trigger=trigger,
        id="daily_planner",
        args=[provider],
        replace_existing=True,
    )
    logger.info("[Planner] Daily planner registered (runs at 00:01).")
