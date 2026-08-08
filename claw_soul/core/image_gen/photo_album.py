"""
Photo album — local storage, JSON index, age-based cleanup.

Layout under ``~/.claw_soul/context/photos/``:

    index.json                  ← chronological metadata index
    2026-05-23_1002_selfie.jpg
    2026-05-23_1604_selfie.jpg
    reference/                  ← user-supplied character reference images
        portrait_01.jpg
        ...
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from typing import Any

from ... import config

logger = logging.getLogger(__name__)


def _photos_dir() -> str:
    return os.path.join(str(config.CLAWSOUL_HOME), "context", "photos")


def _reference_dir() -> str:
    return os.path.join(_photos_dir(), "reference")


class PhotoAlbum:
    """Stores generated photos with metadata, prunes old entries."""

    def __init__(self, root: str | None = None, retention_days: int = 30) -> None:
        self.root = root or _photos_dir()
        self.retention_days = retention_days
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(_reference_dir(), exist_ok=True)

    # ── Index ────────────────────────────────────────────────────────────

    @property
    def _index_path(self) -> str:
        return os.path.join(self.root, "index.json")

    def _load_index(self) -> list[dict[str, Any]]:
        if not os.path.exists(self._index_path):
            return []
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return data.get("entries", [])
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[PhotoAlbum] failed to load index: %s", exc)
            return []

    def _save_index(self, entries: list[dict[str, Any]]) -> None:
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

    # ── Reference images ─────────────────────────────────────────────────

    def list_references(self) -> list[str]:
        d = _reference_dir()
        if not os.path.isdir(d):
            return []
        return sorted(
            os.path.join(d, n) for n in os.listdir(d)
            if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )

    def primary_reference(self) -> str | None:
        refs = self.list_references()
        return refs[0] if refs else None

    # Canonical face reference filename — one per companion, deterministic so
    # the Tigris key (users/<uid>/companion_reference.jpg) is stable too.
    REFERENCE_NAME = "companion_reference.jpg"

    def set_reference(self, src_path: str) -> str:
        """Promote ``src_path`` to THE canonical face reference (replacing any
        existing one).  Returns the stored path."""
        import shutil
        d = _reference_dir()
        os.makedirs(d, exist_ok=True)
        for old in self.list_references():     # one face only — clear the rest
            try:
                os.remove(old)
            except OSError:
                pass
        dst = os.path.join(d, self.REFERENCE_NAME)
        shutil.copyfile(src_path, dst)
        return dst

    def save_reference_bytes(self, data: bytes) -> str:
        """Write raw image bytes as the canonical reference (used when
        restoring it from Tigris on a fresh machine)."""
        d = _reference_dir()
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, self.REFERENCE_NAME)
        with open(dst, "wb") as f:
            f.write(data)
        return dst

    def clear_references(self) -> None:
        """Drop all reference images (e.g. appearance changed on re-customize)."""
        for old in self.list_references():
            try:
                os.remove(old)
            except OSError:
                pass

    # ── Save ─────────────────────────────────────────────────────────────

    def add(self, src_path: str, *, kind: str, prompt: str, metadata: dict[str, Any]) -> str:
        """Move a freshly generated image into the album and index it.

        Returns the new absolute path inside the album.
        """
        if not os.path.isfile(src_path):
            raise FileNotFoundError(src_path)

        now = datetime.now()
        ext = os.path.splitext(src_path)[1] or ".jpg"
        name = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{kind}{ext}"
        dst = os.path.join(self.root, name)

        if os.path.dirname(os.path.realpath(src_path)) != os.path.realpath(self.root):
            shutil.move(src_path, dst)
        else:
            dst = src_path

        entry = {
            "filename": os.path.basename(dst),
            "path": dst,
            "kind": kind,
            "timestamp": now.isoformat(timespec="seconds"),
            "prompt": prompt,
            **metadata,
        }
        entries = self._load_index()
        entries.append(entry)
        self._save_index(entries)
        return dst

    # ── Query ────────────────────────────────────────────────────────────

    def recent(self, days: int = 7, kind: str | None = None) -> list[dict[str, Any]]:
        cutoff = datetime.now() - timedelta(days=days)
        out = []
        for e in self._load_index():
            try:
                ts = datetime.fromisoformat(e["timestamp"])
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            if kind and e.get("kind") != kind:
                continue
            out.append(e)
        return out

    def latest(self, kind: str | None = None) -> dict[str, Any] | None:
        entries = self._load_index()
        for e in reversed(entries):
            if kind is None or e.get("kind") == kind:
                return e
        return None

    # ── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self) -> int:
        """Delete photos older than ``retention_days``. Returns count removed."""
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        entries = self._load_index()
        keep: list[dict[str, Any]] = []
        removed = 0
        for e in entries:
            try:
                ts = datetime.fromisoformat(e["timestamp"])
            except (KeyError, ValueError):
                keep.append(e)
                continue
            if ts < cutoff:
                path = e.get("path", "")
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError as exc:
                        logger.warning("[PhotoAlbum] failed to delete %s: %s", path, exc)
                        keep.append(e)
                        continue
            else:
                keep.append(e)
        self._save_index(keep)
        if removed:
            logger.info("[PhotoAlbum] cleaned up %d photo(s) older than %d days",
                        removed, self.retention_days)
        return removed



