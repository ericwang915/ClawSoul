"""
Bucket list — "things WE want to do together".

Distinct from :class:`~claw_soul.scheduler.wishlist.WishlistManager` in two
ways:

  - **Tense** — wishlist captures fleeting wants the user has *now* ("我想吃日料"),
    surfacing them on a 12h cool-down inside a couple of weeks.
    Bucket list captures *shared aspirations* ("去日本看樱花", "一起拍婚纱照")
    that span months or years and are part of the relationship's identity.

  - **Voice** — wishlist phrasing is in *the user's* third person.  Bucket-list
    entries are phrased as *we*: "we go to Hokkaido in winter".

The bucket list is appended-to by the LLM via ``bucket_add`` whenever it spots
a couple-flavoured aspiration in conversation. Entries are durable — they are
not pruned by recency. ``bucket_mark_done`` is called when the couple actually
does the thing, which feeds the milestone tracker for celebration.

Storage: ``~/.claw_soul/context/bucket_list.json``.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .. import config

logger = logging.getLogger(__name__)


def _path() -> str:
    return os.path.join(str(config.CLAWSOUL_HOME), "context", "bucket_list.json")


@dataclass
class BucketItem:
    id: str
    text: str                          # "去北海道看雪 / Go to Hokkaido in winter"
    category: str = "general"          # general | travel | food | experience | milestone
    status: str = "pending"            # pending | done | dropped
    added_at: str = ""
    done_at: str | None = None
    note: str = ""                     # short context the LLM captured
    tags: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, text: str, category: str = "general",
            note: str = "", tags: list[str] | None = None) -> "BucketItem":
        return cls(
            id=uuid.uuid4().hex[:12],
            text=text.strip(),
            category=category if category in {"general", "travel", "food",
                                              "experience", "milestone"} else "general",
            added_at=datetime.now().isoformat(timespec="seconds"),
            note=note.strip(),
            tags=list(tags or []),
        )


class BucketListManager:
    """JSON-backed shared bucket list with simple CRUD."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _load(self) -> list[BucketItem]:
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [BucketItem(**w) for w in data]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[BucketList] failed to load: %s", exc)
            return []

    def _save(self, items: list[BucketItem]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([asdict(w) for w in items], f, indent=2, ensure_ascii=False)

    # ── CRUD ─────────────────────────────────────────────────────────────

    def add(self, text: str, category: str = "general",
            note: str = "", tags: list[str] | None = None) -> BucketItem:
        items = self._load()
        # Dedupe by identical pending text
        for it in items:
            if it.status == "pending" and it.text.strip() == text.strip():
                return it
        item = BucketItem.new(text, category, note, tags)
        items.append(item)
        self._save(items)
        logger.info("[BucketList] added '%s' (%s)", item.text[:50], item.category)
        return item

    def mark_done(self, item_id: str, note: str = "") -> bool:
        items = self._load()
        for it in items:
            if it.id == item_id and it.status == "pending":
                it.status = "done"
                it.done_at = datetime.now().isoformat(timespec="seconds")
                if note:
                    it.note = (it.note + " — " + note).strip(" —")
                self._save(items)
                return True
        return False

    def drop(self, item_id: str) -> bool:
        items = self._load()
        for it in items:
            if it.id == item_id and it.status == "pending":
                it.status = "dropped"
                self._save(items)
                return True
        return False

    def list_pending(self, category: str | None = None) -> list[BucketItem]:
        items = self._load()
        items = [it for it in items if it.status == "pending"]
        if category:
            items = [it for it in items if it.category == category]
        return items

    def list_done(self) -> list[BucketItem]:
        return [it for it in self._load() if it.status == "done"]

    def stats(self) -> dict[str, Any]:
        items = self._load()
        return {
            "pending": sum(1 for it in items if it.status == "pending"),
            "done":    sum(1 for it in items if it.status == "done"),
            "dropped": sum(1 for it in items if it.status == "dropped"),
            "total":   len(items),
        }
