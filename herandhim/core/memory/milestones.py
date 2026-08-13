"""
Relationship Milestones — track special days, anniversaries, and achievements.

Persisted to ``~/.herandhim/context/relationship/milestones.json``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

_MILESTONE_MEMORY_THRESHOLDS = [50, 100, 200, 500, 1000]
_MILESTONE_PROACTIVE_THRESHOLDS = [10, 50, 100, 200, 500]
_SPECIAL_DAYS = [1, 7, 30, 100, 365, 730, 1095]  # days since first chat

_DATA_DIR = "relationship"


class MilestoneManager:
    """Track relationship milestones and special days.

    State (persisted as JSON):
      - first_chat_date: date string ("YYYY-MM-DD") or None
      - total_memory_entries: int
      - total_proactive_messages: int
      - triggered_milestones: list[str]  (e.g., ["50_memories", "100_days"])
      - last_memory_count_trigger: int
      - last_proactive_count_trigger: int
      - deep_emotion_detected: bool
    """

    def __init__(self, affect_dir: str | None = None) -> None:
        if affect_dir is None:
            from ... import config as _cfg
            affect_dir = os.path.join(
                str(_cfg.HERANDHIM_HOME), "context", _DATA_DIR,
            )
        self._data_dir = affect_dir
        os.makedirs(self._data_dir, exist_ok=True)
        self._path = os.path.join(self._data_dir, "milestones.json")
        self._data: dict[str, Any] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        defaults: dict[str, Any] = {
            "first_chat_date": None,
            "total_memory_entries": 0,
            "total_proactive_messages": 0,
            "triggered_milestones": [],
            "last_memory_count_trigger": 0,
            "last_proactive_count_trigger": 0,
            "deep_emotion_detected": False,
        }
        if not os.path.isfile(self._path):
            self._data = defaults
            self._save()
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._data = {**defaults, **loaded}
        except (OSError, json.JSONDecodeError, ValueError):
            self._data = defaults

    def _save(self) -> None:
        """Atomic write."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(suffix=".json", dir=os.path.dirname(self._path))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning("Failed to save milestones: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _days_since_first(self) -> int | None:
        first = self._data.get("first_chat_date")
        if not first:
            return None
        try:
            d = datetime.strptime(first, "%Y-%m-%d").date()
            return (date.today() - d).days
        except (ValueError, TypeError):
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def ensure_first_chat_date(self, force_date: str | None = None) -> None:
        """Set first_chat_date if not already set.

        *force_date* can be an ISO date string or None (uses today).
        """
        if self._data.get("first_chat_date"):
            return
        self._data["first_chat_date"] = force_date or date.today().isoformat()
        self._save()

    def set_deep_emotion_detected(self) -> None:
        """Mark that the user has shared deep emotions for the first time."""
        if not self._data.get("deep_emotion_detected"):
            self._data["deep_emotion_detected"] = True
            self._data.setdefault("triggered_milestones", [])
            milestone_id = "first_deep_emotion"
            if milestone_id not in self._data["triggered_milestones"]:
                self._data["triggered_milestones"].append(milestone_id)
            self._save()

    def check_milestones(
        self,
        memory_entries: int = 0,
        proactive_count: int = 0,
    ) -> list[str]:
        """Check for newly triggered milestones.

        Returns a list of milestone message strings (empty if none).
        """
        triggered: list[str] = []
        days = self._days_since_first()

        # Update counts
        if memory_entries > 0:
            self._data["total_memory_entries"] = memory_entries
        if proactive_count > 0:
            self._data["total_proactive_messages"] = proactive_count

        # Memory count milestones
        last_mem = self._data.get("last_memory_count_trigger", 0)
        for threshold in _MILESTONE_MEMORY_THRESHOLDS:
            if memory_entries >= threshold > last_mem:
                mid = f"{threshold}_memories"
                if mid not in self._data.get("triggered_milestones", []):
                    self._data.setdefault("triggered_milestones", []).append(mid)
                    self._data["last_memory_count_trigger"] = threshold
                    triggered.append(
                        f"🎉 **{threshold} memories milestone!** "
                        f"You've shared {threshold} things with me."
                    )

        # Proactive message count milestones
        last_pro = self._data.get("last_proactive_count_trigger", 0)
        for threshold in _MILESTONE_PROACTIVE_THRESHOLDS:
            if proactive_count >= threshold > last_pro:
                mid = f"{threshold}_proactive"
                if mid not in self._data.get("triggered_milestones", []):
                    self._data.setdefault("triggered_milestones", []).append(mid)
                    self._data["last_proactive_count_trigger"] = threshold
                    triggered.append(
                        f"🎉 **{threshold} proactive messages milestone!** "
                        f"I've reached out {threshold} times."
                    )

        # Special days
        if days is not None:
            for d in _SPECIAL_DAYS:
                if days == d:
                    mid = f"{d}_days"
                    if mid not in self._data.get("triggered_milestones", []):
                        self._data.setdefault("triggered_milestones", []).append(mid)
                        if d == 1:
                            msg = "🎉 **1 day!** The start of our journey together."
                        elif d == 7:
                            msg = "🎉 **1 week!** A whole week of conversations!"
                        elif d == 30:
                            msg = "🎉 **1 month!** Thanks for sticking with me."
                        elif d == 100:
                            msg = "🎉 **100 days!** That's a real milestone."
                        elif d == 365:
                            msg = "🎉 **1 year!** Happy anniversary! 🎂"
                        elif d == 730:
                            msg = "🎉 **2 years!** What an amazing journey together!"
                        else:
                            msg = f"🎉 **{d} days!** WOW."
                        triggered.append(msg)

        # Deep emotion milestone is handled separately via set_deep_emotion_detected
        # (it's triggered during chat(), not here)

        self._save()
        return triggered

    def is_special_day(self) -> tuple[bool, str]:
        """Check if today is a milestone day.

        Returns (True, "description") or (False, "").
        """
        from .. import lang as _lang
        days = self._days_since_first()
        if days is None:
            return False, ""

        is_cn = _lang.is_chinese()
        if days in (1, 7, 30, 100, 365, 730, 1095):
            if is_cn:
                labels = {1: "认识1天", 7: "认识1周", 30: "认识1个月",
                          100: "认识100天", 365: "认识1周年",
                          730: "认识2周年", 1095: "认识3周年"}
                return True, labels.get(days, f"认识{days}天")
            labels_en = {1: "1 day together", 7: "1 week together",
                         30: "1 month together", 100: "100 days together",
                         365: "1-year anniversary", 730: "2-year anniversary",
                         1095: "3-year anniversary"}
            return True, labels_en.get(days, f"{days} days together")

        # Every 365 days after first year
        if days > 365 and days % 365 == 0:
            years = days // 365
            if is_cn:
                return True, f"认识{years}周年纪念日"
            return True, f"{years}-year anniversary"

        return False, ""

    def get_relationship_age_str(self) -> str:
        """Return human-readable relationship age, e.g. '123 days'."""
        from .. import lang as _lang
        days = self._days_since_first()
        is_cn = _lang.is_chinese()
        if days is None:
            return "正在认识中..." if is_cn else "Still getting to know each other…"
        if days == 0:
            return "初次见面" if is_cn else "Just met"
        if days < 30:
            return f"{days} 天" if is_cn else f"{days} days"
        if days < 365:
            months = days // 30
            remaining = days % 30
            if is_cn:
                return f"{months} 个月 {remaining} 天"
            unit_m = "month" if months == 1 else "months"
            unit_d = "day" if remaining == 1 else "days"
            return f"{months} {unit_m} {remaining} {unit_d}"
        years = days // 365
        remaining = days % 365
        months = remaining // 30
        if is_cn:
            return f"{years} 年 {months} 个月"
        unit_y = "year" if years == 1 else "years"
        unit_m = "month" if months == 1 else "months"
        return f"{years} {unit_y} {months} {unit_m}"

    def get_data(self) -> dict[str, Any]:
        """Return raw data dict."""
        return dict(self._data)
