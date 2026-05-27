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
    city: str = "Shanghai",
) -> str:
    """Fetch today's weather summary. Tries multiple providers, then falls back.

    Providers in order:
      1. Open-Meteo (free, no key) — often blocked from Fly Singapore region
      2. wttr.in (free, no key, mirrors many sources) — reliable fallback
      3. Seasonal fallback (no network) — last resort
    """
    fetched = _try_open_meteo(lat, lon) or _try_wttr_in(city)
    if fetched:
        return fetched
    logger.info("[Planner] All weather providers failed — using seasonal fallback")
    return _seasonal_fallback_weather()


def _try_open_meteo(lat: float, lon: float) -> str | None:
    """Return formatted weather, or None on failure."""
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
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("[Planner] Open-Meteo unavailable: %s", exc)
        return None

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


def _try_wttr_in(city: str) -> str | None:
    """wttr.in mirrors several weather sources and is generally reachable
    from regions where Open-Meteo's edge gets 502/504. JSON via ``?format=j1``.
    """
    # wttr.in can be slow (it fans out to several upstream weather APIs);
    # give it more time than the typical free API.
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1&lang=zh"
        req = urllib.request.Request(url, headers={
            "User-Agent": "curl/7.88.1",  # wttr.in tunes its response for curl UA
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("[Planner] wttr.in unavailable: %s", exc)
        return None

    current = (data.get("current_condition") or [{}])[0]
    today = (data.get("weather") or [{}])[0]

    temp_now = current.get("temp_C", "?")
    desc_zh = ""
    for d in current.get("lang_zh") or []:
        if d.get("value"):
            desc_zh = d["value"]
            break
    if not desc_zh:
        desc_zh = (current.get("weatherDesc") or [{}])[0].get("value", "")
    humidity = current.get("humidity")
    precip_mm = current.get("precipMM")
    hi = today.get("maxtempC")
    lo = today.get("mintempC")

    parts = [f"当前天气：{desc_zh or '未知'}，{temp_now}°C"]
    if hi and lo:
        parts.append(f"今日气温：{lo}°C ~ {hi}°C")
    if humidity:
        parts.append(f"湿度：{humidity}%")
    if precip_mm and float(precip_mm) > 0:
        parts.append(f"降水量：{precip_mm}mm")
    return "\n".join(parts)


def _seasonal_fallback_weather() -> str:
    """Best-effort Shanghai-style weather when Open-Meteo is unreachable.

    The downstream prompt prefers having ANY signal over a blank line, so we
    pick a plausible string from the month + a tiny bit of jitter. Better than
    leaving the LLM to invent August snow.
    """
    import random as _r
    m = datetime.now().month
    bands = {
        1:  [("阴冷干燥", 3, 8), ("小雨偏冷", 4, 9)],
        2:  [("阴天偏冷", 5, 11), ("小雨初春", 7, 13)],
        3:  [("多云转晴", 10, 16), ("阴有阵雨", 9, 14)],
        4:  [("多云回暖", 14, 22), ("阴有小雨", 12, 18)],
        5:  [("多云偏热", 19, 27), ("阴有阵雨", 17, 23)],
        6:  [("梅雨潮湿", 22, 28), ("多云闷热", 24, 30)],
        7:  [("晴热高温", 28, 34), ("午后雷阵雨", 26, 32)],
        8:  [("晴热高温", 27, 33), ("午后雷阵雨", 26, 31)],
        9:  [("多云转晴", 22, 28), ("阵雨转凉", 20, 25)],
        10: [("秋高气爽", 16, 23), ("阴天转凉", 14, 19)],
        11: [("阴冷多云", 10, 16), ("小雨偏凉", 8, 13)],
        12: [("阴冷干燥", 4, 10), ("阵雨偏冷", 5, 11)],
    }
    cond, lo, hi = _r.choice(bands.get(m, [("天气一般", 15, 22)]))
    return (
        f"当前天气：{cond}（实时数据暂不可用，按季节估）\n"
        f"今日气温：{lo}°C ~ {hi}°C"
    )


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


def _gather_realtime_signals(city: str = "Shanghai", weekday_zh: str = "") -> str:
    """Pull a small grounded brief about today: local events + headlines.

    Uses Tavily web search if configured; returns empty string otherwise so
    the planner stays honest (no inventing concert names when nothing was
    actually fetched). The planner prompt below leans on this — when the
    block is empty, it falls back to vague, season-typical activities.
    """
    try:
        from ..core.tools import web_search
        # Probe whether Tavily is configured for this tenant.
        if not config.get_str("tavily", "apiKey", env="TAVILY_API_KEY"):
            return ""
    except Exception:
        return ""

    today_str = datetime.now().strftime("%Y-%m-%d")
    queries = [
        f"{city} 今日活动 演出 展览 {today_str}",
        f"{city} 本周 活动 演唱会 市集 美食节",
    ]

    parts: list[str] = []
    for q in queries:
        try:
            result = web_search(
                query=q,
                search_depth="basic",
                topic="general",
                max_results=4,
                time_range="week",
            )
        except Exception as exc:
            logger.debug("[Planner] web_search failed (%s): %s", q, exc)
            continue
        if not result or result.startswith("Error"):
            continue
        # Trim each search payload — we want grounding, not transcript dumps.
        snippet = result.strip()
        if len(snippet) > 1500:
            snippet = snippet[:1500] + "\n…(truncated)"
        parts.append(f"### 查询「{q}」\n{snippet}")
    return "\n\n".join(parts)


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

    # Real-time signals (weather + local events) — both run off the executor
    # so the planner doesn't block on slow weather/search providers.
    loop = asyncio.get_running_loop()
    weather, realtime_signals = await asyncio.gather(
        loop.run_in_executor(None, _fetch_weather_brief),
        loop.run_in_executor(None, _gather_realtime_signals),
    )

    # Random daily mood
    mood = random.choice(_MOODS)

    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    weekday = _WEEKDAYS_ZH[now.weekday()]
    season = _season(now.month)
    holiday_info = _get_holiday_info(now)

    realtime_block = (
        f"\n## 实时检索到的本地信息（一手资料，优先使用）\n{realtime_signals}\n"
        if realtime_signals else
        "\n## 实时检索\n（无实时检索结果——保持模糊，不要编造具体活动名称、嘉宾、地点）\n"
    )

    prompt = f"""你要为自己规划今天一整天的日程。你就是下面这个角色，请完全代入。
这份日程是给你自己看的——你之后跟男朋友/女朋友聊天时，会根据当下时间在
日程里走到哪一格来决定自己「现在大概在干嘛、心情怎么样、刚发生了什么」。
所以细节越具体越好，越像真人随手记的生活流水越好。

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
{realtime_block}

## 要求

以第一人称从早 7 点到次日 0-1 点（睡觉），**输出至少 36 个时间槽**，
每 20-40 分钟一格，重要时段（早起、吃饭、工作、和对方聊天的窗口）可以
更密一点。时间往前走，**不要倒序**；时间格式 `HH:MM`。

**每一格至少 2 个具体细节**，不能只写"画画"或"吃饭"这种动词。比如：

```
- 09:15 闹钟响第三次才肯爬起来，芝麻已经在脚边喵了五分钟饭点要迟到了；
        揉着眼睛去给它添粮，自己泡了杯冰美式坐在窗边发呆
- 10:00 打开数位板继续昨晚那张水彩，主色定的偏冷调；
        放了张坂本龙一的专辑，画到 11 点手腕酸了停下来动动
- 12:30 下楼便利店随便买了个三明治，店员小哥今天又戴了那个鸭舌帽
```

## 内容规则

1. **完全代入角色**：职业 / 作息 / 爱好 / 所在城市的真实节奏。
2. **每格 1-2 个感官或情绪细节**：天气声音、房间气味、身体感受、
   芝麻的状态、看到的东西、想到的事、随手发的消息等。
3. **天气驱动行为**：根据上面的"天气"字段决定室内 / 室外。下雨就别
   出门、闷热就只去有空调的、晴天可以约人散步。天气未知时按季节
   合理猜（5 月末上海多云偏凉，午后可能阵雨）。
4. **日期 / 周末 / 假期**：
   - 周一到周五：必须有工作内容（接稿 / 改稿 / 沟通客户 / 提案）；
   - 周六周日：作息后移、更松散，有 brunch / 约朋友 / 逛街 / 看展；
   - 节假日（春节、清明、五一、端午、中秋、国庆、圣诞、跨年等）：
     体现仪式感（吃饺子、看烟花、跨年倒数等），出现节日相关消费场景
     （市集 / 灯会 / 限定店 / 礼物准备）；
   - 节假日临近时：体现期待感（"明天就放假了""下周国庆要回家"）。
5. **当地活动（沉浸感最重要）**——一天里安排 1-3 件本地能干的事。
   **严格规则——不能瞎编**：
   * 如果上面「实时检索到的本地信息」有具体活动 / 演出 / 展览 / 新店 →
     **优先采用实际检索到的事件**，名字、地点、日期都可以直接用。
   * 如果实时检索为空 → **必须保持模糊**，不要编具体的真实活动名、
     艺人、嘉宾、街区门牌号。可以泛泛地说"想去附近某家新开的咖啡馆"、
     "刷到有个市集挺有意思想去看看"，但**不要说**"今晚去看周杰伦演唱会"
     或"在 SMP 看 XX 的演出"这种具体到名字的假信息。
   * 通用安全方向：散步、逛家居店、探小红书种草新店、看新上映电影
     （不写具体片名）、去常去的咖啡店、菜场、楼下花店——这些不会出错。
   * 季节性的模糊参考（**仅作风格提示，不要直接照抄活动名**）：
     - 春末夏初（5-6 月）：户外咖啡市集、美术馆春展、外滩散步
     - 夏天（7-8 月）：夜市、夏日艺术活动、夜跑、商场里乘凉
     - 秋天（9-11 月）：草地活动、艺术周、十一长假出行
     - 冬天（12-2 月）：圣诞市集、跨年活动、年货置办
   * **示例对比**：
     好："傍晚要去看那个新开的草本咖啡馆，听说店里有只橘猫"
     好（有实时检索数据时）："晚上要去 XX 美术馆看那个春季展，
            听说今天最后一天"
     坏："去看 2026 年 5 月 26 日上海大舞台某具体艺人演唱会"
     坏："今晚 XX 路 XX 号的 live house 有 XX 乐队"
6. **每天 3-5 个「锚点」事件**：和 Sarah brunch、收快递、拆盲盒、去画材
   店、看楼下小猫、刷到笑出声的视频、和阿凯线上聊画技、网购到货、
   买花、吃到新店、和家人视频。
7. **预留和对方互动的窗口**：早起想发早安、下午摸鱼想分享、傍晚走在
   路上突然想念、洗完澡躺下想聊聊今天——这种触发点散布在一天里。
8. **必要的"无聊"时段**：刷手机、发呆、走神、躺平——真人不会每分钟
   都在做有意义的事。
9. **偶尔小意外或转折**：临时改主意不出门、画得不顺重画、被外卖小哥
   电话吵醒、本想去的地方排队太长换地方。
10. **不要每格都阳光积极**：偶尔可以累、烦、莫名其妙地丧、对客户生气。
11. 一天结束于 0:00-1:00 之间睡觉。

直接输出日程列表（**至少 36 行** `- HH:MM ...`），不要标题、不要多余说明。
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

        logger.info("[Planner] Successfully generated today's schedule → %s (%d chars)", plan_file, len(content))
    except Exception as exc:
        logger.error("[Planner] Failed to generate daily plan: %s", exc, exc_info=True)


# ── Scheduler registration ───────────────────────────────────────────────────

def register_daily_planner(
    scheduler: AsyncIOScheduler,
    provider: LLMProvider,
) -> None:
    """Register the daily planner cron job (runs at 00:01 local time)."""
    trigger = CronTrigger(hour=0, minute=1)
    scheduler.add_job(
        generate_daily_plan,
        trigger=trigger,
        id="daily_planner",
        args=[provider],
        replace_existing=True,
    )
    tz_label = scheduler.timezone if hasattr(scheduler, 'timezone') else "default"
    logger.info("[Planner] Daily planner registered (runs at 00:01 %s).", tz_label)
