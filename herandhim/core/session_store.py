"""
Markdown-backed session store for herandhim.

Each session gets its own Markdown file with timestamped messages::

    context/sessions/telegram_1285451567.md

File format
-----------
Human-readable Markdown with embedded metadata in HTML comments for reliable
round-trip parsing.  Each message block::

    <!-- msg:{"role":"user","ts":"2026-02-23T15:18:58"} -->
    ### 2026-02-23 15:18:58 — User

    Hello, how are you?

    ---

Tool calls are stored as JSON code blocks inside the message.  System
injection messages (skill loads, compaction summaries) are also recorded.

Truncation
----------
When a session file grows beyond *max_messages*, older messages are dropped
(keeping only the most recent ones by timestamp).  The system prompt
(messages[0]) is never saved — it is always rebuilt fresh on restore.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

def _default_store_dir() -> str:
    from .. import config as _cfg
    return os.path.join(str(_cfg.HERANDHIM_HOME), "context", "sessions")


DEFAULT_STORE_DIR = None  # resolved lazily
DEFAULT_MAX_MESSAGES = 50

_META_PATTERN = re.compile(r"<!-- msg:(.*?) -->")
_ROLE_LABELS = {
    "user": "User",
    "assistant": "Assistant",
    "system": "System",
    "tool": "Tool",
}


class SessionStore:
    """Reads and writes per-session message history as Markdown files."""

    def __init__(
        self,
        base_dir: str | None = None,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> None:
        self.base_dir = base_dir or _default_store_dir()
        self.max_messages = max_messages
        os.makedirs(self.base_dir, exist_ok=True)

    # ── File path ─────────────────────────────────────────────────────────────

    def _path(self, session_id: str) -> str:
        """Convert a session_id like 'telegram:123' to a safe filename."""
        safe = re.sub(r"[^\w\-]", "_", session_id)
        return os.path.join(self.base_dir, f"{safe}.md")

    # ── Serialisation ─────────────────────────────────────────────────────────

    @staticmethod
    def _msg_to_markdown(msg: dict) -> str:
        """Convert a single message dict to a Markdown block.

        Handles both plain-string content and OpenAI multimodal content
        (a list of ``{type:"text"|"image_url", …}`` parts). Image parts
        are flattened to a short placeholder — we don't write the base64
        bytes back to the session file, since they're already too big
        for human-readable storage and the LLM doesn't need them for
        replay (they were only relevant in the live turn).
        """
        role = msg.get("role", "unknown")
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            chunks: list[str] = []
            for part in raw_content:
                if not isinstance(part, dict):
                    chunks.append(str(part))
                elif part.get("type") == "text":
                    chunks.append(str(part.get("text", "")))
                elif part.get("type") == "image_url":
                    chunks.append("[image]")
                else:
                    chunks.append(f"[{part.get('type', 'part')}]")
            content = "\n".join(c for c in chunks if c)
        else:
            content = raw_content or ""
        ts = msg.get("_ts") or datetime.now().isoformat(timespec="seconds")

        # Build metadata for round-trip parsing
        meta: dict = {"role": role, "ts": ts}
        if msg.get("tool_call_id"):
            meta["tool_call_id"] = msg["tool_call_id"]

        meta_json = json.dumps(meta, ensure_ascii=False)
        label = _ROLE_LABELS.get(role, role.title())

        # Format timestamp for display
        try:
            dt = datetime.fromisoformat(ts)
            display_ts = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            display_ts = ts

        lines = [f"<!-- msg:{meta_json} -->"]
        lines.append(f"### {display_ts} — {label}")
        lines.append("")

        if content:
            lines.append(content)
            lines.append("")

        # Embed tool_calls as JSON
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            lines.append("<details><summary>Tool Calls</summary>")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(tool_calls, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _parse_markdown(text: str) -> list[dict]:
        """Parse a session Markdown file back into message dicts."""
        messages: list[dict] = []

        # Split into blocks by the HTML comment markers
        blocks = _META_PATTERN.split(text)
        # blocks = [preamble, meta1, content1, meta2, content2, ...]

        i = 1  # skip preamble (title / header)
        while i < len(blocks) - 1:
            meta_str = blocks[i].strip()
            body = blocks[i + 1].strip()
            i += 2

            try:
                meta = json.loads(meta_str)
            except json.JSONDecodeError:
                continue

            role = meta.get("role", "unknown")
            msg: dict = {"role": role}

            if meta.get("tool_call_id"):
                msg["tool_call_id"] = meta["tool_call_id"]
            if meta.get("ts"):
                msg["_ts"] = meta["ts"]

            # Extract content: everything between the header line and
            # optional <details> / --- markers
            content_lines = []
            tool_calls_json = None
            in_details = False
            in_json_block = False
            json_lines: list[str] = []

            for line in body.split("\n"):
                stripped = line.strip()

                # Skip the ### header line and trailing ---
                if stripped.startswith("### ") or stripped == "---":
                    continue

                if stripped == "<details><summary>Tool Calls</summary>":
                    in_details = True
                    continue
                if stripped == "</details>":
                    in_details = False
                    continue

                if in_details:
                    if stripped == "```json":
                        in_json_block = True
                        continue
                    if stripped == "```" and in_json_block:
                        in_json_block = False
                        try:
                            tool_calls_json = json.loads("\n".join(json_lines))
                        except json.JSONDecodeError:
                            pass
                        json_lines = []
                        continue
                    if in_json_block:
                        json_lines.append(line)
                        continue
                    continue

                content_lines.append(line)

            content = "\n".join(content_lines).strip()
            if content:
                msg["content"] = content
            else:
                msg["content"] = ""

            if tool_calls_json:
                msg["tool_calls"] = tool_calls_json

            messages.append(msg)

        return messages

    # ── Core API ──────────────────────────────────────────────────────────────

    def save(self, session_id: str, messages: list[dict]) -> None:
        """
        Persist messages[1:] to Markdown.
        messages[0] is the initial system prompt — always rebuilt fresh.
        """
        to_save = messages[1:] if len(messages) > 1 else []

        # Add timestamps to messages that don't have one
        for msg in to_save:
            if "_ts" not in msg:
                msg["_ts"] = datetime.now().isoformat(timespec="seconds")

        # Truncate by time — keep only the most recent max_messages
        if len(to_save) > self.max_messages:
            to_save = to_save[-self.max_messages:]

        path = self._path(session_id)
        try:
            lines = [f"# Session: {session_id}\n\n"]
            for msg in to_save:
                lines.append(self._msg_to_markdown(msg))
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError as exc:
            logger.error("[SessionStore] Failed to save session '%s': %s", session_id, exc)

    def load(self, session_id: str) -> list[dict]:
        """
        Return saved messages (messages[1:] from a previous run).
        Applies time-based truncation: only the most recent max_messages
        are returned.
        """
        path = self._path(session_id)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            logger.error("[SessionStore] Failed to load session '%s': %s", session_id, exc)
            return []

        messages = self._parse_markdown(text)

        # Time-based truncation: keep only the most recent messages
        if len(messages) > self.max_messages:
            messages = messages[-self.max_messages:]

        return messages

    def delete(self, session_id: str) -> None:
        """Remove the Markdown file for session_id."""
        path = self._path(session_id)
        if os.path.exists(path):
            try:
                os.remove(path)
                logger.info("[SessionStore] Deleted session '%s'", session_id)
            except OSError as exc:
                logger.error("[SessionStore] Failed to delete '%s': %s", session_id, exc)

    def list_session_ids(self) -> list[str]:
        """Return all session IDs that have a persisted Markdown file."""
        ids = []
        for fname in os.listdir(self.base_dir):
            if fname.endswith(".md"):
                ids.append(fname[: -len(".md")])
        return ids
