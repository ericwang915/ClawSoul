"""
Tests for session persistence (SessionStore + PersistentAgent)
and Agent-controlled cron tools.
"""
import json
import os
from unittest.mock import MagicMock


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_fake_provider(content="pong"):
    provider = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    msg.model_dump.return_value = {"role": "assistant", "content": content}
    choice = MagicMock()
    choice.message = msg
    provider.chat.return_value = MagicMock(choices=[choice])
    return provider


def make_minimal_agent(tmp_path, provider=None, store=None, session_id="test:1", cron_manager=None):
    """Build a PersistentAgent (or plain Agent) with minimal context."""
    from herandhim.core.persistent_agent import PersistentAgent
    from herandhim.core.agent import Agent

    memory_dir = str(tmp_path / "memory")
    os.makedirs(memory_dir, exist_ok=True)
    skills_dir = str(tmp_path / "skills")
    os.makedirs(skills_dir, exist_ok=True)

    provider = provider or make_fake_provider()

    if store is not None:
        return PersistentAgent(
            provider=provider,
            memory_dir=memory_dir,
            skills_dirs=[skills_dir],
            knowledge_path=None,
            persona_path=None,
            soul_path=None,
            verbose=False,
            store=store,
            session_id=session_id,
            cron_manager=cron_manager,
        )
    return Agent(
        provider=provider,
        memory_dir=memory_dir,
        skills_dirs=[skills_dir],
        knowledge_path=None,
        persona_path=None,
        soul_path=None,
        verbose=False,
        cron_manager=cron_manager,
    )


# ── SessionStore ─────────────────────────────────────────────────────────────

class TestSessionStore:
    def test_save_and_load_roundtrip(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))
        messages = [
            {"role": "system", "content": "sys"},   # index 0 — NOT saved
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        store.save("test:1", messages)
        loaded = store.load("test:1")
        # messages[0] (system prompt) is not saved
        assert len(loaded) == 2
        assert loaded[0]["content"] == "hello"
        assert loaded[1]["content"] == "hi"

    def test_load_missing_returns_empty(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))
        assert store.load("nonexistent") == []

    def test_delete_removes_file(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))
        store.save("test:1", [{"role": "system", "content": "s"}, {"role": "user", "content": "x"}])
        store.delete("test:1")
        assert store.load("test:1") == []

    def test_path_sanitises_colons(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))
        store.save("telegram:123", [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}])
        files = os.listdir(str(tmp_path / "sessions"))
        assert any("telegram" in f for f in files)
        assert not any(":" in f for f in files)

    def test_list_session_ids(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))
        store.save("a:1", [{"role": "system", "content": "s"}, {"role": "user", "content": "x"}])
        store.save("b:2", [{"role": "system", "content": "s"}, {"role": "user", "content": "y"}])
        ids = store.list_session_ids()
        assert len(ids) == 2


# ── PersistentAgent ──────────────────────────────────────────────────────────

class TestPersistentAgent:
    def test_chat_saves_to_store(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))
        agent = make_minimal_agent(tmp_path, store=store, session_id="test:1")
        agent.chat("hello")
        loaded = store.load("test:1")
        contents = [m["content"] for m in loaded]
        assert "hello" in contents

    def test_restore_on_second_init(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))

        # First agent: sends a message
        a1 = make_minimal_agent(tmp_path, store=store, session_id="test:1")
        a1.chat("remember this")

        # Second agent: same session_id → should restore history
        a2 = make_minimal_agent(tmp_path, store=store, session_id="test:1")
        contents = [m["content"] for m in a2.messages]
        assert "remember this" in contents

    def test_fresh_system_prompt_on_restore(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))

        a1 = make_minimal_agent(tmp_path, store=store, session_id="test:1")
        a1.chat("hello")

        a2 = make_minimal_agent(tmp_path, store=store, session_id="test:1")
        # messages[0] should be the freshly rebuilt system prompt
        # (content may differ slightly due to Soul Mate affect context updates
        #  — milestone dates, relationship state — but the structure matches)
        assert a2.messages[0]["role"] == "system"
        assert "HerAndHim agent" in a2.messages[0]["content"]
        assert "### Tools" in a2.messages[0]["content"]
        assert "### Response Guidelines" in a2.messages[0]["content"]

    def test_reset_clears_history(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        from herandhim.session_manager import SessionManager
        store = SessionStore(str(tmp_path / "sessions"))

        def factory(sid):
            return make_minimal_agent(tmp_path, store=store, session_id=sid)

        sm = SessionManager(factory, store=store)
        a1 = sm.get_or_create("test:1")
        a1.chat("I should be forgotten")

        # Reset deletes the JSONL
        a2 = sm.reset("test:1")
        # New agent should start fresh (only system prompt)
        non_system = [m for m in a2.messages if m.get("role") != "system"]
        assert len(non_system) == 0

    def test_compact_also_saves(self, tmp_path):
        from herandhim.core.session_store import SessionStore
        store = SessionStore(str(tmp_path / "sessions"))
        agent = make_minimal_agent(tmp_path, store=store, session_id="test:1")

        # Add enough history to compact
        for i in range(10):
            agent.messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"turn {i}",
            })

        agent.compact()
        loaded = store.load("test:1")
        # Store should reflect post-compaction state
        assert len(loaded) > 0


# ── Agent cron tools ─────────────────────────────────────────────────────────

class TestAgentCronTools:
    """Tests for CronScheduler dynamic job management (no event loop needed)."""

    def _make_cron_manager(self, tmp_path):
        """Return a CronScheduler with a mock scheduler (no event loop required)."""
        from herandhim.scheduler.cron import CronScheduler
        from herandhim.session_manager import SessionManager
        sm = SessionManager(lambda sid: MagicMock())
        jobs_path = str(tmp_path / "jobs.yaml")
        with open(jobs_path, "w") as f:
            f.write("jobs: []\n")
        c = CronScheduler(session_manager=sm, jobs_path=jobs_path)
        # Replace the AsyncIOScheduler with a synchronous mock so tests don't need a loop
        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = []
        mock_scheduler.get_job.return_value = None
        mock_scheduler.add_job = MagicMock()
        mock_scheduler.remove_job = MagicMock()
        c._scheduler = mock_scheduler
        return c

    def test_cron_manager_attached_to_agent(self, tmp_path):
        cron_mgr = self._make_cron_manager(tmp_path)
        agent = make_minimal_agent(tmp_path, cron_manager=cron_mgr)
        assert agent._cron_manager is cron_mgr

    def test_cron_add_returns_confirmation(self, tmp_path):
        cron_mgr = self._make_cron_manager(tmp_path)
        result = cron_mgr.add_dynamic_job(
            job_id="test_job",
            cron_expr="0 9 * * *",
            prompt="Good morning!",
        )
        assert "test_job" in result
        assert "scheduled" in result.lower()

    def test_cron_add_persisted_to_json(self, tmp_path):
        from herandhim.scheduler.cron import _dynamic_jobs_file as _djf; DYNAMIC_JOBS_FILE = _djf()
        cron_mgr = self._make_cron_manager(tmp_path)
        cron_mgr.add_dynamic_job("persist_job", "0 8 * * *", "Daily check")
        assert os.path.exists(DYNAMIC_JOBS_FILE)
        with open(DYNAMIC_JOBS_FILE) as f:
            data = json.load(f)
        assert "persist_job" in data
        assert data["persist_job"]["cron"] == "0 8 * * *"

    def test_cron_remove_existing(self, tmp_path):
        cron_mgr = self._make_cron_manager(tmp_path)
        cron_mgr.add_dynamic_job("rm_job", "0 9 * * *", "Remove me")
        result = cron_mgr.remove_dynamic_job("rm_job")
        assert "removed" in result.lower()

        # Should no longer be in JSON
        from herandhim.scheduler.cron import _dynamic_jobs_file as _djf; DYNAMIC_JOBS_FILE = _djf()
        with open(DYNAMIC_JOBS_FILE) as f:
            data = json.load(f)
        assert "rm_job" not in data

    def test_cron_remove_nonexistent(self, tmp_path):
        cron_mgr = self._make_cron_manager(tmp_path)
        result = cron_mgr.remove_dynamic_job("ghost_job")
        assert "not found" in result.lower()

    def test_cron_list_empty(self, tmp_path):
        cron_mgr = self._make_cron_manager(tmp_path)
        result = cron_mgr.list_jobs()
        assert "No scheduled" in result

    def test_cron_list_shows_added_job(self, tmp_path):
        from herandhim.scheduler.cron import CronScheduler
        from herandhim.session_manager import SessionManager
        import datetime

        sm = SessionManager(lambda sid: MagicMock())
        jobs_path = str(tmp_path / "jobs.yaml")
        with open(jobs_path, "w") as f:
            f.write("jobs: []\n")
        c = CronScheduler(session_manager=sm, jobs_path=jobs_path)

        # Mock scheduler with one job
        mock_job = MagicMock()
        mock_job.id = "my_dynamic_job"
        mock_job.next_run_time = datetime.datetime(2026, 3, 1, 9, 0, tzinfo=datetime.timezone.utc)
        c._scheduler = MagicMock()
        c._scheduler.get_jobs.return_value = [mock_job]

        # Fake the dynamic_jobs.json
        from herandhim.scheduler.cron import _dynamic_jobs_file as _djf; DYNAMIC_JOBS_FILE = _djf()
        os.makedirs(os.path.dirname(DYNAMIC_JOBS_FILE), exist_ok=True)
        with open(DYNAMIC_JOBS_FILE, "w") as f:
            json.dump({"my_dynamic_job": {"cron": "0 9 * * *", "prompt": "hi"}}, f)

        result = c.list_jobs()
        assert "my_dynamic_job" in result
        assert "[dynamic]" in result

    def test_load_dynamic_jobs_from_json(self, tmp_path):
        from herandhim.scheduler.cron import _dynamic_jobs_file as _djf; DYNAMIC_JOBS_FILE = _djf()
        os.makedirs(os.path.dirname(DYNAMIC_JOBS_FILE), exist_ok=True)
        with open(DYNAMIC_JOBS_FILE, "w") as f:
            json.dump({
                "pre_existing": {"cron": "0 7 * * *", "prompt": "Early bird"}
            }, f)

        cron_mgr = self._make_cron_manager(tmp_path)
        jobs = cron_mgr._load_dynamic_jobs()
        assert "pre_existing" in jobs
        assert jobs["pre_existing"]["cron"] == "0 7 * * *"

    def teardown_method(self, method):
        from herandhim.scheduler.cron import _dynamic_jobs_file as _djf; DYNAMIC_JOBS_FILE = _djf()
        if os.path.exists(DYNAMIC_JOBS_FILE):
            os.remove(DYNAMIC_JOBS_FILE)
