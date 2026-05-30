"""
Scene builder — turn the daily plan + current time into a scene description.

Reads ``context/calendar/today_plan.md`` (produced by scheduler/planner.py)
and finds the activity nearest to *now*. The result feeds into the selfie
prompt as the "what she's doing right now" portion.

Plan format (from planner.py):

    # 2026-05-23（周六）日程

    > 普通周六
    > 北京 18-25°C 多云
    > 精神状态：今天心情很好

    - 09:30 赖在床上刷手机
    - 10:00 起床冲咖啡
    - 11:00 在画室画水彩
    ...
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime

from ... import config

logger = logging.getLogger(__name__)

_ACTIVITY_RE = re.compile(r"^\s*-\s*(\d{1,2}):(\d{2})\s+(.+?)\s*$")
# Heuristics for classifying header lines that have no explicit "key：value" form.
_WEATHER_HINTS = (
    "°C", "℃", "晴", "云", "雨", "雪", "雾", "霾", "风", "阴",
    "sunny", "cloud", "rain", "snow", "fog",
)


@dataclass
class Scene:
    time: str = ""
    activity: str = ""
    mood: str = ""
    weather: str = ""
    holiday: str = ""
    outfit: str = ""

    def is_empty(self) -> bool:
        return not (self.activity or self.mood or self.weather or self.outfit)

    def as_prompt_block(self) -> str:
        """Render the scene as a Chinese natural-language paragraph for Seedream."""
        if self.is_empty():
            return ""
        parts: list[str] = []
        when = self.time or datetime.now().strftime("%H:%M")
        if self.activity:
            parts.append(f"{when} 她正在{self.activity}")
        if self.outfit:
            parts.append(f"今天穿着{self.outfit}")
        if self.weather:
            parts.append(f"窗外{self.weather}")
        if self.mood:
            parts.append(f"心情是「{self.mood}」")
        return "，".join(parts) + "。"


def _plan_path() -> str:
    return os.path.join(
        str(config.CLAWSOUL_HOME),
        "context", "calendar", "today_plan.md",
    )


def _parse_plan(text: str) -> tuple[dict[str, str], list[tuple[int, str]]]:
    """Return (header_fields, [(minutes_since_midnight, activity), ...])."""
    header: dict[str, str] = {}
    activities: list[tuple[int, str]] = []
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith(">"):
            body = line.lstrip("> ").strip()
            if not body:
                continue
            if "：" in body:
                key, _, val = body.partition("：")
                header[key.strip()] = val.strip()
            elif ":" in body and not body[0].isdigit():
                key, _, val = body.partition(":")
                header[key.strip()] = val.strip()
            elif any(hint in body for hint in _WEATHER_HINTS):
                header.setdefault("天气", body)
            else:
                header.setdefault("节日", body)
            continue
        m = _ACTIVITY_RE.match(line)
        if m:
            hh, mm, act = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            activities.append((hh * 60 + mm, act))
    activities.sort()
    return header, activities


def _pick_current(activities: list[tuple[int, str]], now_minutes: int) -> tuple[str, str]:
    """Find the activity scheduled at or just before ``now_minutes``."""
    if not activities:
        return "", ""
    chosen = activities[0]
    for slot in activities:
        if slot[0] <= now_minutes:
            chosen = slot
        else:
            break
    hh, mm = divmod(chosen[0], 60)
    return f"{hh:02d}:{mm:02d}", chosen[1]


def build_scene(now: datetime | None = None) -> Scene:
    """Read today's plan and return a Scene describing the current moment."""
    now = now or datetime.now()
    path = _plan_path()
    if not os.path.isfile(path):
        logger.debug("[scene_builder] no plan at %s — returning empty scene", path)
        return Scene()
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        logger.warning("[scene_builder] failed to read plan: %s", exc)
        return Scene()

    header, activities = _parse_plan(text)
    time_str, activity = _pick_current(activities, now.hour * 60 + now.minute)
    weather = header.get("天气", "")

    # Daily, weather-coupled, persona-appropriate outfit. Deterministic by date,
    # so the selfie and what she says in chat agree. Best-effort — never break
    # scene building over wardrobe.
    outfit = ""
    try:
        from . import wardrobe
        lang = config.get_str("agent", "language", default="en") or "en"
        outfit = wardrobe.outfit_today(now, weather, lang, activity=activity)
    except Exception as exc:
        logger.debug("[scene_builder] outfit skipped: %s", exc)

    return Scene(
        time=time_str,
        activity=activity,
        mood=header.get("精神状态") or header.get("心情") or "",
        weather=weather,
        holiday=header.get("节日", ""),
        outfit=outfit,
    )
