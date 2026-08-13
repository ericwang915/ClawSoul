"""Verify the clear_chat_history tool wires up correctly."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from herandhim.core import tools


def test_clear_chat_history_is_in_memory_tools():
    names = [t["function"]["name"] for t in tools.MEMORY_TOOLS]
    assert "clear_chat_history" in names


def test_clear_chat_history_has_optional_reason_param():
    decl = next(
        t for t in tools.MEMORY_TOOLS
        if t["function"]["name"] == "clear_chat_history"
    )
    params = decl["function"]["parameters"]
    assert "reason" in params["properties"]
    # `reason` is optional (informative only)
    assert params.get("required", []) == []


def test_agent_dispatch_sets_pending_flag(monkeypatch, tmp_path):
    """Dispatching clear_chat_history must set the deferred flag, NOT wipe
    messages mid-turn (would break the running tool loop)."""
    from herandhim import config
    from herandhim.core.agent import Agent

    monkeypatch.setattr(config, "_HERANDHIM_BASE", tmp_path)
    config._configs.clear()

    # Build a minimal Agent — provider is mocked to avoid LLM calls
    provider = MagicMock()
    provider.supports_images = False

    with patch.object(Agent, "_init_system_prompt"):
        a = Agent.__new__(Agent)
        # The dispatch function reads self.memory etc., but for this test
        # we only need the clear branch. Stub the bare attributes used.
        a._pending_clear_history = False
        a._pending_clear_reason = ""
        a.messages = []
        a.loaded_skill_names = set()
        a.compaction_count = 3
        a._pending_attachments = [{"type": "image_url", "image_url": {"url": "x"}}]
        a.verbose = False

        # Directly invoke the dispatch branch via a synthetic tool call object
        tc = MagicMock()
        tc.function.name = "clear_chat_history"
        tc.function.arguments = '{"reason": "user wants a clean slate"}'
        tc.id = "call_x"

        # Call the real method that runs the branch
        result = Agent._execute_tool_call(a, tc)

    assert "cleared after this turn" in result
    assert a._pending_clear_history is True
    assert "clean slate" in a._pending_clear_reason
    # Messages NOT wiped yet — that's the whole point of deferral
    assert a.compaction_count == 3
    assert a._pending_attachments  # not cleared mid-turn


def test_maybe_clear_after_turn_runs_clear():
    from herandhim.core.agent import Agent

    with patch.object(Agent, "_init_system_prompt"):
        a = Agent.__new__(Agent)
        a._pending_clear_history = True
        a._pending_clear_reason = "test"
        a.messages = [{"role": "system", "content": "x"},
                      {"role": "user", "content": "y"}]
        a.loaded_skill_names = {"some_skill"}
        a.compaction_count = 5
        a._pending_attachments = [{"x": 1}]

        a._maybe_clear_history_after_turn()

    # clear_history() rebuilds system prompt — verified by it being called
    # via _init_system_prompt patch (no exception means it ran)
    assert a._pending_clear_history is False
    assert a._pending_clear_reason == ""
    assert a.loaded_skill_names == set()
    assert a.compaction_count == 0
    assert a._pending_attachments == []


def test_maybe_clear_is_noop_without_flag():
    from herandhim.core.agent import Agent

    a = Agent.__new__(Agent)
    a._pending_clear_history = False
    a._pending_clear_reason = ""
    a.messages = [{"role": "user", "content": "hi"}]
    a.loaded_skill_names = {"k"}

    a._maybe_clear_history_after_turn()

    # Nothing was cleared
    assert a.messages == [{"role": "user", "content": "hi"}]
    assert a.loaded_skill_names == {"k"}
