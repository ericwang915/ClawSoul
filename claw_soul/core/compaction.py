"""
Context compaction for claw_soul.

Compaction summarises older conversation history into a compact summary entry
and keeps recent messages intact — preventing context-window overflows in long
sessions while preserving important information.

Inspired by openclaw's compaction model:
  https://docs.openclaw.ai/concepts/compaction

How it works
------------
1. Split chat history into "old" (to summarise) + "recent" (to keep verbatim).
2. Memory flush — silently ask the LLM to extract key facts from the old
   messages and persist them via the agent's `remember` tool, so nothing
   important is lost permanently.
3. Summarise — call the LLM with a summarisation prompt to produce a concise
   paragraph covering decisions, facts, tasks, and open questions.
4. Persist — append the summary to `context/compaction/history.jsonl` as an
   audit trail.
5. Replace — swap the old messages with a single [Compaction Summary] system
   message and increment the compaction counter.

Token estimation
----------------
We don't bundle a tokeniser, so we use a conservative character-based
approximation: 1 token ≈ 4 characters.  This is good enough for triggering
auto-compaction; the actual model enforces the hard limit.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base import LLMProvider
    from .memory.manager import MemoryManager

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
DEFAULT_AUTO_THRESHOLD_TOKENS = 6000   # trigger auto-compaction at ~6k tokens
DEFAULT_RECENT_KEEP = 6                # keep last N chat messages verbatim
def _compaction_log_file() -> str:
    from .. import config as _cfg
    return os.path.join(str(_cfg.CLAWSOUL_HOME), "context", "compaction", "history.jsonl")


COMPACTION_LOG_FILE = None  # resolved lazily


# ── Token estimation ──────────────────────────────────────────────────────────

def estimate_tokens(messages: list[dict]) -> int:
    """Rough character-based token estimate for a list of messages."""
    total_chars = sum(len(str(m.get("content") or "")) for m in messages)
    return total_chars // CHARS_PER_TOKEN


# ── JSONL persistence ─────────────────────────────────────────────────────────

def persist_compaction(summary: str, message_count: int, log_path: str | None = None) -> None:
    """Append one compaction entry to the JSONL audit log."""
    log_path = log_path or _compaction_log_file()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "summarised_messages": message_count,
        "summary": summary,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.debug("[Compaction] Persisted to %s", log_path)


# ── Message → plain text ──────────────────────────────────────────────────────

def messages_to_text(messages: list[dict]) -> str:
    """Convert a message list to a readable transcript for summarisation."""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if role == "assistant" and not content and m.get("tool_calls"):
            content = f"[called tools: {[tc.get('function', {}).get('name') if isinstance(tc, dict) else tc.function.name for tc in m.get('tool_calls', [])]}]"
        if role == "tool":
            content = f"[tool result]: {content[:300]}{'...' if len(content) > 300 else ''}"
        if content:
            lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


# ── Memory flush ──────────────────────────────────────────────────────────────

def memory_flush(
    messages_to_flush: list[dict],
    provider: "LLMProvider",
    memory: "MemoryManager",
) -> int:
    """
    Silent LLM call that extracts key facts from old messages and saves them
    to long-term memory before those messages are discarded.

    Returns the number of facts saved.
    """
    if not messages_to_flush:
        return 0

    history_text = messages_to_text(messages_to_flush)
    prompt = (
        "You are a memory extraction assistant. "
        "Given the following conversation transcript, identify ALL important facts, "
        "decisions, preferences, and context that should be remembered long-term. "
        "Return a JSON array of objects with 'key' and 'value' fields. "
        "If nothing important, return [].\n\n"
        f"TRANSCRIPT:\n{history_text}\n\n"
        "Return ONLY valid JSON, no explanation."
    )
    try:
        response = provider.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            tool_choice="none",
        )
        raw = response.choices[0].message.content or "[]"
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        facts: list[dict] = json.loads(raw)
        saved = 0
        for fact in facts:
            key = str(fact.get("key", "")).strip()
            value = str(fact.get("value", "")).strip()
            if key and value:
                memory.remember(value, key)
                saved += 1
        logger.info("[Compaction] Memory flush saved %d fact(s).", saved)
        return saved
    except Exception as exc:
        logger.warning("[Compaction] Memory flush failed (non-fatal): %s", exc)
        return 0


# ── Core compact function ─────────────────────────────────────────────────────

def compact(
    messages: list[dict],
    provider: "LLMProvider",
    memory: "MemoryManager | None" = None,
    recent_keep: int = DEFAULT_RECENT_KEEP,
    instruction: str | None = None,
    log_path: str | None = None,
) -> tuple[list[dict], str]:
    """
    Compact conversation history.

    Parameters
    ----------
    messages      : full message list (system + chat)
    provider      : LLM provider used for summarisation
    memory        : MemoryManager for pre-compaction memory flush (optional)
    recent_keep   : number of recent chat messages to keep verbatim
    instruction   : optional extra focus hint for the summarisation prompt
    log_path      : where to persist the compaction JSONL entry

    Returns
    -------
    (new_messages, summary_text)
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    chat_msgs   = [m for m in messages if m.get("role") != "system"]

    if len(chat_msgs) <= recent_keep:
        logger.info("[Compaction] Not enough history to compact (%d messages).", len(chat_msgs))
        return messages, ""

    to_summarise = chat_msgs[:-recent_keep]
    to_keep      = chat_msgs[-recent_keep:]

    logger.info(
        "[Compaction] Summarising %d message(s), keeping %d recent.",
        len(to_summarise), len(to_keep),
    )

    # 1. Memory flush — save important facts before discarding old messages
    if memory is not None:
        memory_flush(to_summarise, provider, memory)

    # 2. Summarise
    history_text = messages_to_text(to_summarise)
    focus = f"\nAdditional focus: {instruction}" if instruction else ""
    summarise_prompt = (
        f"Summarise the following conversation history concisely. "
        f"Focus on: decisions made, key facts learned, tasks completed, open questions.{focus}\n\n"
        f"CONVERSATION:\n{history_text}\n\n"
        f"Provide a compact summary (3–8 sentences or bullet points):"
    )
    try:
        response = provider.chat(
            messages=[{"role": "user", "content": summarise_prompt}],
            tools=[],
            tool_choice="none",
        )
        summary = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error("[Compaction] Summarisation failed: %s", exc)
        raise

    # 3. Persist
    persist_compaction(summary, len(to_summarise), log_path=log_path)

    # 4. Build new message list
    summary_system_msg = {
        "role": "system",
        "content": f"[Compaction Summary — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]\n{summary}",
    }
    new_messages = system_msgs + [summary_system_msg] + to_keep

    return new_messages, summary
