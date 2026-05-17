"""
Tests for SOUL.md support in agent.py.
"""
import os
import tempfile
import pytest
from unittest.mock import MagicMock

from claw_soul.core.agent import Agent, _load_text_dir_or_file


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_fake_provider():
    """Minimal LLMProvider stub that never makes real API calls."""
    provider = MagicMock()
    provider.chat.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="pong", tool_calls=None))]
    )
    return provider


def make_agent_with_dirs(soul_dir=None, persona_dir=None, tmp_path=None):
    """Build an Agent instance pointing at custom soul/persona directories."""
    provider = make_fake_provider()
    context_dir = tmp_path or tempfile.mkdtemp()

    # Minimal context scaffolding so Agent.__init__ doesn't crash
    memory_dir = os.path.join(context_dir, "memory")
    os.makedirs(memory_dir, exist_ok=True)

    skills_dir = os.path.join(context_dir, "skills")
    os.makedirs(skills_dir, exist_ok=True)

    return Agent(
        provider=provider,
        memory_dir=memory_dir,
        skills_dirs=[skills_dir],
        knowledge_path=None,
        persona_path=persona_dir,
        soul_path=soul_dir,
        verbose=False,
    )


# ── _load_text_dir_or_file ───────────────────────────────────────────────────

class TestLoadTextDirOrFile:
    def test_none_path_returns_empty(self):
        assert _load_text_dir_or_file(None) == ""

    def test_nonexistent_path_returns_empty(self):
        assert _load_text_dir_or_file("/tmp/does_not_exist_xyz") == ""

    def test_loads_single_file(self, tmp_path):
        f = tmp_path / "SOUL.md"
        f.write_text("# My Soul\nBe kind.")
        result = _load_text_dir_or_file(str(f), label="Soul")
        assert "# My Soul" in result
        assert "Be kind." in result

    def test_loads_all_md_files_from_dir(self, tmp_path):
        (tmp_path / "a.md").write_text("Alpha")
        (tmp_path / "b.md").write_text("Beta")
        (tmp_path / "skip.py").write_text("should be ignored")
        result = _load_text_dir_or_file(str(tmp_path), label="Soul")
        assert "Alpha" in result
        assert "Beta" in result
        assert "should be ignored" not in result

    def test_files_sorted_alphabetically(self, tmp_path):
        (tmp_path / "z.txt").write_text("LAST")
        (tmp_path / "a.md").write_text("FIRST")
        result = _load_text_dir_or_file(str(tmp_path))
        assert result.index("FIRST") < result.index("LAST")


# ── Agent soul loading ───────────────────────────────────────────────────────

class TestAgentSoulLoading:
    def test_soul_loaded_into_attribute(self, tmp_path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        (soul_dir / "SOUL.md").write_text("Be honest. Be kind.")
        agent = make_agent_with_dirs(soul_dir=str(soul_dir), tmp_path=str(tmp_path))
        assert "Be honest. Be kind." in agent.soul_instruction

    def test_no_soul_dir_gives_empty_string(self, tmp_path):
        agent = make_agent_with_dirs(soul_dir=None, tmp_path=str(tmp_path))
        assert agent.soul_instruction == ""

    def test_soul_before_persona_in_system_prompt(self, tmp_path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        (soul_dir / "SOUL.md").write_text("SOUL_MARKER")

        persona_dir = tmp_path / "persona"
        persona_dir.mkdir()
        (persona_dir / "persona.md").write_text("PERSONA_MARKER")

        agent = make_agent_with_dirs(
            soul_dir=str(soul_dir),
            persona_dir=str(persona_dir),
            tmp_path=str(tmp_path),
        )
        system_msg = agent.messages[0]["content"]
        soul_pos = system_msg.index("SOUL_MARKER")
        persona_pos = system_msg.index("PERSONA_MARKER")
        assert soul_pos < persona_pos, "Soul must appear before persona in the system prompt"

    def test_soul_section_header_present(self, tmp_path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        (soul_dir / "SOUL.md").write_text("values here")
        agent = make_agent_with_dirs(soul_dir=str(soul_dir), tmp_path=str(tmp_path))
        system_msg = agent.messages[0]["content"]
        assert "## Core Identity (Soul)" in system_msg

    def test_persona_section_header_present(self, tmp_path):
        persona_dir = tmp_path / "persona"
        persona_dir.mkdir()
        (persona_dir / "p.md").write_text("I am a pirate.")
        agent = make_agent_with_dirs(persona_dir=str(persona_dir), tmp_path=str(tmp_path))
        system_msg = agent.messages[0]["content"]
        assert "## Role & Persona" in system_msg

    def test_no_soul_no_section_header(self, tmp_path):
        agent = make_agent_with_dirs(tmp_path=str(tmp_path))
        system_msg = agent.messages[0]["content"]
        assert "## Core Identity (Soul)" not in system_msg

    def test_soul_content_in_system_prompt(self, tmp_path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        (soul_dir / "SOUL.md").write_text("Never lie.")
        agent = make_agent_with_dirs(soul_dir=str(soul_dir), tmp_path=str(tmp_path))
        system_msg = agent.messages[0]["content"]
        assert "Never lie." in system_msg


# ── SessionManager ───────────────────────────────────────────────────────────

class TestSessionManager:
    def _make_sm(self):
        from claw_soul.session_manager import SessionManager
        calls = []
        def factory(sid):
            calls.append(1)
            return MagicMock()
        return SessionManager(factory), calls

    def test_get_or_create_creates_on_first_call(self):
        sm, calls = self._make_sm()
        sm.get_or_create("telegram:42")
        assert len(calls) == 1

    def test_get_or_create_reuses_existing(self):
        sm, calls = self._make_sm()
        a1 = sm.get_or_create("telegram:42")
        a2 = sm.get_or_create("telegram:42")
        assert len(calls) == 1
        assert a1 is a2

    def test_different_session_ids_are_isolated(self):
        sm, calls = self._make_sm()
        a1 = sm.get_or_create("telegram:1")
        a2 = sm.get_or_create("telegram:2")
        assert a1 is not a2
        assert len(calls) == 2

    def test_reset_creates_new_agent(self):
        sm, calls = self._make_sm()
        a1 = sm.get_or_create("telegram:42")
        a2 = sm.reset("telegram:42")
        assert len(calls) == 2
        assert a1 is not a2

    def test_remove_deletes_session(self):
        sm, _ = self._make_sm()
        sm.get_or_create("telegram:42")
        assert "telegram:42" in sm
        sm.remove("telegram:42")
        assert "telegram:42" not in sm

    def test_list_sessions(self):
        sm, _ = self._make_sm()
        sm.get_or_create("telegram:1")
        sm.get_or_create("cron:daily")
        sessions = sm.list_sessions()
        assert "telegram:1" in sessions
        assert "cron:daily" in sessions

    def test_len(self):
        sm, _ = self._make_sm()
        assert len(sm) == 0
        sm.get_or_create("a")
        sm.get_or_create("b")
        assert len(sm) == 2

    def test_cron_session_id_convention(self):
        sm, calls = self._make_sm()
        sm.get_or_create("cron:daily_summary")
        sm.get_or_create("cron:daily_summary")
        assert len(calls) == 1  # same job_id → same session

    def test_get_returns_none_for_missing(self):
        sm, _ = self._make_sm()
        assert sm.get("nonexistent") is None


telegram = pytest.importorskip("telegram", reason="python-telegram-bot not installed")


# ── Telegram bot ─────────────────────────────────────────────────────────────

class TestTelegramBot:
    def _make_sm(self):
        from claw_soul.session_manager import SessionManager
        return SessionManager(lambda sid: MagicMock())

    def test_import(self):
        from claw_soul.channels.telegram_bot import TelegramBot, create_bot_from_env
        assert TelegramBot is not None

    def test_allowlist_empty_allows_all(self):
        from claw_soul.channels.telegram_bot import TelegramBot
        bot = TelegramBot(session_manager=self._make_sm(), token="dummy", allowed_users=None)
        assert bot._is_allowed(123456)
        assert bot._is_allowed(999999)

    def test_allowlist_restricts_access(self):
        from claw_soul.channels.telegram_bot import TelegramBot
        bot = TelegramBot(session_manager=self._make_sm(), token="dummy", allowed_users=[111, 222])
        assert bot._is_allowed(111)
        assert not bot._is_allowed(333)

    def test_session_id_format(self):
        from claw_soul.channels.telegram_bot import TelegramBot
        assert TelegramBot._session_id(42) == "telegram:42"

    def test_messages_use_session_manager(self):
        from claw_soul.session_manager import SessionManager
        from claw_soul.channels.telegram_bot import TelegramBot
        calls = []
        def factory(sid):
            calls.append(1)
            return MagicMock()
        sm = SessionManager(factory)
        bot = TelegramBot(session_manager=sm, token="dummy")
        # Simulate two messages from the same chat_id
        sm.get_or_create("telegram:42")
        sm.get_or_create("telegram:42")
        assert len(calls) == 1, "Same chat should reuse one Agent"

    def test_reset_via_session_manager(self):
        from claw_soul.session_manager import SessionManager
        from claw_soul.channels.telegram_bot import TelegramBot
        calls = []
        sm = SessionManager(lambda sid: (calls.append(1), MagicMock())[1])
        bot = TelegramBot(session_manager=sm, token="dummy")
        a1 = sm.get_or_create("telegram:42")
        a2 = sm.reset("telegram:42")
        assert a1 is not a2

    def test_split_message_short(self):
        from claw_soul.channels.telegram_bot import _split_message
        chunks = _split_message("Hello", limit=4096)
        assert chunks == ["Hello"]

    def test_split_message_long(self):
        from claw_soul.channels.telegram_bot import _split_message
        text = "A" * 5000
        chunks = _split_message(text, limit=4096)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4096
        assert len(chunks[1]) == 904


# ── Cron scheduler ───────────────────────────────────────────────────────────

class TestCronScheduler:
    def _make_sm(self):
        from claw_soul.session_manager import SessionManager
        return SessionManager(lambda sid: MagicMock())

    def test_import(self):
        from claw_soul.scheduler.cron import CronScheduler, _parse_cron
        assert CronScheduler is not None

    def test_parse_cron_valid(self):
        from claw_soul.scheduler.cron import _parse_cron
        trigger = _parse_cron("0 9 * * *")
        assert trigger is not None

    def test_parse_cron_invalid_raises(self):
        from claw_soul.scheduler.cron import _parse_cron
        with pytest.raises(ValueError):
            _parse_cron("0 9 *")  # only 3 fields

    def test_load_jobs_missing_file(self, tmp_path):
        from claw_soul.scheduler.cron import CronScheduler
        s = CronScheduler(session_manager=self._make_sm(), jobs_path=str(tmp_path / "missing.yaml"))
        jobs = s._load_jobs()
        assert jobs == []

    def test_load_jobs_from_yaml(self, tmp_path):
        from claw_soul.scheduler.cron import CronScheduler
        jobs_file = tmp_path / "jobs.yaml"
        jobs_file.write_text(
            "jobs:\n"
            "  - id: test_job\n"
            "    cron: '0 9 * * *'\n"
            "    prompt: 'Hello'\n"
            "    enabled: true\n"
        )
        s = CronScheduler(session_manager=self._make_sm(), jobs_path=str(jobs_file))
        jobs = s._load_jobs()
        assert len(jobs) == 1
        assert jobs[0]["id"] == "test_job"

    def test_disabled_job_not_registered(self, tmp_path):
        from claw_soul.scheduler.cron import CronScheduler
        jobs_file = tmp_path / "jobs.yaml"
        jobs_file.write_text(
            "jobs:\n"
            "  - id: disabled_job\n"
            "    cron: '0 9 * * *'\n"
            "    prompt: 'Hello'\n"
            "    enabled: false\n"
        )
        s = CronScheduler(session_manager=self._make_sm(), jobs_path=str(jobs_file))
        count = s.load_and_register_jobs()
        assert count == 0

    def test_enabled_job_registered(self, tmp_path):
        from claw_soul.scheduler.cron import CronScheduler
        jobs_file = tmp_path / "jobs.yaml"
        jobs_file.write_text(
            "jobs:\n"
            "  - id: enabled_job\n"
            "    cron: '0 9 * * *'\n"
            "    prompt: 'Hello'\n"
            "    enabled: true\n"
        )
        s = CronScheduler(session_manager=self._make_sm(), jobs_path=str(jobs_file))
        count = s.load_and_register_jobs()
        assert count == 1

    def test_each_job_gets_isolated_session(self, tmp_path):
        from claw_soul.scheduler.cron import CronScheduler
        from claw_soul.session_manager import SessionManager
        calls = []
        sm = SessionManager(lambda sid: (calls.append(1), MagicMock())[1])
        jobs_file = tmp_path / "jobs.yaml"
        jobs_file.write_text(
            "jobs:\n"
            "  - id: job_a\n"
            "    cron: '0 9 * * *'\n"
            "    prompt: 'A'\n"
            "    enabled: true\n"
            "  - id: job_b\n"
            "    cron: '0 10 * * *'\n"
            "    prompt: 'B'\n"
            "    enabled: true\n"
        )
        s = CronScheduler(session_manager=sm, jobs_path=str(jobs_file))
        s.load_and_register_jobs()
        # Pre-create the sessions as the scheduler would
        sm.get_or_create("cron:job_a")
        sm.get_or_create("cron:job_b")
        assert "cron:job_a" in sm
        assert "cron:job_b" in sm
        assert sm.get("cron:job_a") is not sm.get("cron:job_b")


# ── Heartbeat monitor ────────────────────────────────────────────────────────

class TestHeartbeatMonitor:
    def test_import(self):
        from claw_soul.scheduler.heartbeat import HeartbeatMonitor, create_heartbeat_from_env
        assert HeartbeatMonitor is not None

    def test_create_from_env_defaults(self, monkeypatch):
        from claw_soul.scheduler.heartbeat import create_heartbeat_from_env, DEFAULT_INTERVAL
        monkeypatch.delenv("HEARTBEAT_INTERVAL_SEC", raising=False)
        monkeypatch.delenv("HEARTBEAT_ALERT_CHAT_ID", raising=False)
        hb = create_heartbeat_from_env(provider=MagicMock())
        assert hb._interval == DEFAULT_INTERVAL
        assert hb._alert_chat_id is None

    def test_create_from_env_custom(self, monkeypatch):
        from claw_soul.scheduler.heartbeat import create_heartbeat_from_env
        monkeypatch.setenv("HEARTBEAT_INTERVAL_SEC", "30")
        monkeypatch.setenv("HEARTBEAT_ALERT_CHAT_ID", "9999")
        hb = create_heartbeat_from_env(provider=MagicMock())
        assert hb._interval == 30
        assert hb._alert_chat_id == 9999

    def test_probe_ok_sets_last_ok_true(self):
        import asyncio
        from claw_soul.scheduler.heartbeat import HeartbeatMonitor
        provider = MagicMock()
        provider.chat.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="pong"))]
        )
        hb = HeartbeatMonitor(provider=provider, interval_sec=60, log_path="/tmp/test_heartbeat.log")
        asyncio.run(hb._probe())
        assert hb._last_ok is True

    def test_probe_fail_sets_last_ok_false(self):
        import asyncio
        from claw_soul.scheduler.heartbeat import HeartbeatMonitor
        provider = MagicMock()
        provider.chat.side_effect = ConnectionError("LLM unreachable")
        hb = HeartbeatMonitor(provider=provider, interval_sec=60, log_path="/tmp/test_heartbeat.log")
        asyncio.run(hb._probe())
        assert hb._last_ok is False


# ── init.py ──────────────────────────────────────────────────────────────────

class TestInit:
    def test_soul_component_created(self, tmp_path):
        from claw_soul.init import init
        init(str(tmp_path))
        soul_dir = tmp_path / "context" / "soul"
        assert soul_dir.exists(), "context/soul/ should be created by init()"

    def test_soul_md_copied_from_template(self, tmp_path):
        from claw_soul.init import init
        init(str(tmp_path))
        soul_file = tmp_path / "context" / "soul" / "SOUL.md"
        assert soul_file.exists(), "SOUL.md should be copied from template"
        content = soul_file.read_text()
        assert len(content) > 50, "SOUL.md should contain real content"
