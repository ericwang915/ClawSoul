"""
Tests for the compaction module and its integration in Agent.
"""
import json
import os
from unittest.mock import MagicMock

from claw_soul.core.compaction import (
    estimate_tokens,
    messages_to_text,
    persist_compaction,
    compact,
    memory_flush,
    CHARS_PER_TOKEN,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_provider(summary="This is a summary."):
    """Return a mock LLMProvider whose chat() always returns the given summary."""
    provider = MagicMock()
    provider.chat.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=summary))]
    )
    return provider


def make_provider_with_flush(summary="This is a summary.", facts=None):
    """
    Provider that returns JSON facts on the first call (memory flush)
    and the summary string on the second call (summarisation).
    """
    if facts is None:
        facts = []

    responses = [
        json.dumps(facts),  # call 1: memory flush
        summary,            # call 2: summarisation
    ]
    call_iter = iter(responses)

    def _chat(**kwargs):
        content = next(call_iter, summary)
        return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])

    provider = MagicMock()
    provider.chat.side_effect = _chat
    return provider


def chat_messages(n=10):
    """Build a simple alternating user/assistant chat history."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Message {i}"})
    return msgs


# ── estimate_tokens ──────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens([]) == 0

    def test_single_message(self):
        msgs = [{"role": "user", "content": "A" * 400}]
        assert estimate_tokens(msgs) == 400 // CHARS_PER_TOKEN

    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "A" * 200},
            {"role": "assistant", "content": "B" * 200},
        ]
        assert estimate_tokens(msgs) == 400 // CHARS_PER_TOKEN

    def test_none_content_safe(self):
        msgs = [{"role": "assistant", "content": None}]
        assert estimate_tokens(msgs) == 0


# ── messages_to_text ─────────────────────────────────────────────────────────

class TestMessagesToText:
    def test_basic_transcript(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        text = messages_to_text(msgs)
        assert "USER: Hello" in text
        assert "ASSISTANT: Hi there" in text

    def test_tool_result_truncated(self):
        msgs = [{"role": "tool", "content": "x" * 500}]
        text = messages_to_text(msgs)
        assert "..." in text
        assert len(text) < 600

    def test_empty_messages_excluded(self):
        msgs = [{"role": "user", "content": ""}]
        text = messages_to_text(msgs)
        assert text == ""


# ── persist_compaction ───────────────────────────────────────────────────────

class TestPersistCompaction:
    def test_creates_jsonl_file(self, tmp_path):
        log_file = str(tmp_path / "history.jsonl")
        persist_compaction("Test summary", 5, log_path=log_file)
        assert os.path.exists(log_file)

    def test_jsonl_entry_valid(self, tmp_path):
        log_file = str(tmp_path / "history.jsonl")
        persist_compaction("My summary", 3, log_path=log_file)
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["summary"] == "My summary"
        assert entry["summarised_messages"] == 3
        assert "ts" in entry

    def test_multiple_entries_appended(self, tmp_path):
        log_file = str(tmp_path / "history.jsonl")
        persist_compaction("Summary A", 2, log_path=log_file)
        persist_compaction("Summary B", 4, log_path=log_file)
        with open(log_file) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["summary"] == "Summary B"


# ── compact() core function ──────────────────────────────────────────────────

class TestCompactFunction:
    def test_returns_new_messages_and_summary(self, tmp_path):
        log = str(tmp_path / "h.jsonl")
        system = [{"role": "system", "content": "You are ADA."}]
        chat = chat_messages(10)
        provider = make_provider(summary="Older messages summarised here.")
        new_msgs, summary = compact(
            messages=system + chat,
            provider=provider,
            recent_keep=4,
            log_path=log,
        )
        assert summary == "Older messages summarised here."
        assert len(new_msgs) > 0

    def test_recent_messages_preserved_verbatim(self, tmp_path):
        log = str(tmp_path / "h.jsonl")
        system = [{"role": "system", "content": "sys"}]
        chat = chat_messages(10)
        recent_keep = 4
        provider = make_provider()
        new_msgs, _ = compact(
            messages=system + chat,
            provider=provider,
            recent_keep=recent_keep,
            log_path=log,
        )
        # The last `recent_keep` chat messages must appear unchanged
        kept_contents = {m["content"] for m in new_msgs if m.get("role") != "system"}
        expected_contents = {m["content"] for m in chat[-recent_keep:]}
        assert expected_contents.issubset(kept_contents)

    def test_summary_injected_as_system_message(self, tmp_path):
        log = str(tmp_path / "h.jsonl")
        provider = make_provider(summary="Compact summary text.")
        new_msgs, _ = compact(
            messages=[{"role": "system", "content": "sys"}] + chat_messages(10),
            provider=provider,
            recent_keep=4,
            log_path=log,
        )
        system_msgs = [m for m in new_msgs if m.get("role") == "system"]
        compaction_msgs = [m for m in system_msgs if "Compaction Summary" in m.get("content", "")]
        assert len(compaction_msgs) == 1
        assert "Compact summary text." in compaction_msgs[0]["content"]

    def test_not_enough_history_returns_unchanged(self, tmp_path):
        log = str(tmp_path / "h.jsonl")
        msgs = [{"role": "system", "content": "sys"}] + chat_messages(3)
        provider = make_provider()
        new_msgs, summary = compact(
            messages=msgs,
            provider=provider,
            recent_keep=6,
            log_path=log,
        )
        assert summary == ""
        assert new_msgs == msgs

    def test_original_system_msgs_preserved(self, tmp_path):
        log = str(tmp_path / "h.jsonl")
        system = [
            {"role": "system", "content": "SYSTEM_A"},
            {"role": "system", "content": "SYSTEM_B"},
        ]
        provider = make_provider()
        new_msgs, _ = compact(
            messages=system + chat_messages(10),
            provider=provider,
            recent_keep=4,
            log_path=log,
        )
        contents = [m["content"] for m in new_msgs if m.get("role") == "system"]
        assert "SYSTEM_A" in contents
        assert "SYSTEM_B" in contents

    def test_jsonl_persisted(self, tmp_path):
        log = str(tmp_path / "h.jsonl")
        provider = make_provider(summary="stored summary")
        compact(
            messages=[{"role": "system", "content": "s"}] + chat_messages(10),
            provider=provider,
            recent_keep=4,
            log_path=log,
        )
        assert os.path.exists(log)
        with open(log) as f:
            entry = json.loads(f.readline())
        assert entry["summary"] == "stored summary"


# ── memory_flush ─────────────────────────────────────────────────────────────

class TestMemoryFlush:
    def _make_memory(self):
        storage = {}
        mem = MagicMock()
        mem.remember.side_effect = lambda content, key: storage.update({key: content})
        return mem, storage

    def test_saves_facts_to_memory(self):
        provider = MagicMock()
        provider.chat.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='[{"key":"name","value":"Alice"}]'))]
        )
        mem, storage = self._make_memory()
        saved = memory_flush(chat_messages(4), provider, mem)
        assert saved == 1
        assert storage.get("name") == "Alice"

    def test_invalid_json_does_not_crash(self):
        provider = MagicMock()
        provider.chat.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="not json"))]
        )
        mem = MagicMock()
        # Should return 0, not raise
        result = memory_flush(chat_messages(4), provider, mem)
        assert result == 0

    def test_provider_error_does_not_crash(self):
        provider = MagicMock()
        provider.chat.side_effect = ConnectionError("oops")
        mem = MagicMock()
        result = memory_flush(chat_messages(4), provider, mem)
        assert result == 0


# ── Agent.compact() integration ──────────────────────────────────────────────

class TestAgentCompact:
    def _make_agent(self, tmp_path):
        from claw_soul.core.agent import Agent
        provider = MagicMock()
        provider.chat.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="[]"))]
        )

        memory_dir = str(tmp_path / "memory")
        os.makedirs(memory_dir, exist_ok=True)
        skills_dir = str(tmp_path / "skills")
        os.makedirs(skills_dir, exist_ok=True)

        return Agent(
            provider=provider,
            memory_dir=memory_dir,
            skills_dirs=[skills_dir],
            knowledge_path=None,
            persona_path=None,
            soul_path=None,
            verbose=False,
            compaction_recent_keep=4,
        )

    def test_compact_increments_count(self, tmp_path):
        agent = self._make_agent(tmp_path)
        # Add enough chat history
        for i in range(10):
            agent.messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"})
        agent.compact()
        assert agent.compaction_count == 1

    def test_compact_not_enough_history(self, tmp_path):
        agent = self._make_agent(tmp_path)
        # Only 2 chat messages — below recent_keep=4
        agent.messages.append({"role": "user", "content": "hi"})
        agent.messages.append({"role": "assistant", "content": "hello"})
        result = agent.compact()
        assert agent.compaction_count == 0
        assert "Nothing to compact" in result

    def test_auto_compaction_triggered(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.auto_compaction = True
        agent.compaction_threshold = 10  # very low threshold

        # Add messages that exceed threshold
        for i in range(6):
            agent.messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 50})

        agent._maybe_auto_compact()
        assert agent.compaction_count == 1

    def test_auto_compaction_disabled(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.auto_compaction = False
        agent.compaction_threshold = 1  # would trigger if enabled

        for i in range(6):
            agent.messages.append({"role": "user", "content": "x" * 100})

        triggered = agent._maybe_auto_compact()
        assert not triggered
        assert agent.compaction_count == 0
