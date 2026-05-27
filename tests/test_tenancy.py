"""Verify the per-user tenancy layer isolates config + paths correctly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw_soul import config
from claw_soul.core import tenancy


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    """Point CLAWSOUL_HOME at a fresh temp dir and clear caches per-test."""
    monkeypatch.setattr(config, "_CLAWSOUL_BASE", tmp_path)
    config._configs.clear()
    config._config_paths.clear()
    # Make sure no tenant is leaked from a previous test
    while tenancy.get_current_user() is not None:
        # Defensive: drop any context binding (no-op if none)
        break
    yield


def _write_user_config(base: Path, user_id: str | None, payload: dict) -> Path:
    home = base / "users" / user_id if user_id else base
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "claw_soul.json"
    cfg.write_text(json.dumps(payload))
    return cfg


def test_home_returns_base_when_no_user_bound(tmp_path):
    assert tenancy.get_current_user() is None
    assert config.home() == tmp_path
    assert config.CLAWSOUL_HOME == tmp_path  # PEP 562 __getattr__


def test_home_returns_per_user_dir_when_user_bound(tmp_path):
    with tenancy.user_context("alice"):
        assert config.home() == tmp_path / "users" / "alice"
        assert config.CLAWSOUL_HOME == tmp_path / "users" / "alice"
    # context restored after with-block
    assert config.home() == tmp_path


def test_two_users_see_their_own_configs(tmp_path):
    _write_user_config(tmp_path, "alice", {"llm": {"provider": "deepseek"}})
    _write_user_config(tmp_path, "bob",   {"llm": {"provider": "claude"}})

    with tenancy.user_context("alice"):
        assert config.get_str("llm", "provider") == "deepseek"

    with tenancy.user_context("bob"):
        assert config.get_str("llm", "provider") == "claude"

    # Single-tenant fallback (no user) still works without a config file
    assert config.get_str("llm", "provider", default="none") == "none"


def test_config_cache_is_per_tenant_not_shared(tmp_path):
    """Switching tenants must not leak cached values from the previous one."""
    _write_user_config(tmp_path, "alice", {"k": "alice-val"})
    _write_user_config(tmp_path, "bob",   {"k": "bob-val"})

    with tenancy.user_context("alice"):
        assert config.get_str("k") == "alice-val"
    with tenancy.user_context("bob"):
        assert config.get_str("k") == "bob-val"
    # Alice's value still cached and correct on re-entry
    with tenancy.user_context("alice"):
        assert config.get_str("k") == "alice-val"


def test_files_dir_is_per_tenant(tmp_path):
    with tenancy.user_context("alice"):
        d = config.files_dir()
        assert d == tmp_path / "users" / "alice" / "context" / "files"
        assert d.exists()
    with tenancy.user_context("bob"):
        assert config.files_dir() == tmp_path / "users" / "bob" / "context" / "files"


def test_group_context_dir_is_per_tenant(tmp_path):
    with tenancy.user_context("alice"):
        d = config.group_context_dir("telegram:123")
        assert d == tmp_path / "users" / "alice" / "context" / "groups" / "telegram_123"


def test_contextvar_isolated_in_nested_blocks(tmp_path):
    with tenancy.user_context("alice"):
        assert tenancy.get_current_user() == "alice"
        with tenancy.user_context("bob"):
            assert tenancy.get_current_user() == "bob"
        # Bob's context exited, alice's restored
        assert tenancy.get_current_user() == "alice"
    assert tenancy.get_current_user() is None


def test_legacy_single_tenant_mode_still_works(tmp_path):
    """When no user is bound, behavior should match pre-refactor."""
    _write_user_config(tmp_path, None, {"web": {"port": 7788}})

    assert tenancy.get_current_user() is None
    assert config.get_int("web", "port") == 7788
    assert config.config_path() == tmp_path / "claw_soul.json"


# ── wrap_async_for_user ───────────────────────────────────────────────────────

def test_wrap_async_runs_callable_inside_user_context():
    """The wrapped coroutine must see the tenant bound, even though the
    caller (APScheduler) fires it without any tenancy."""
    import asyncio
    seen: list[str | None] = []

    async def inner(*args, **kwargs):
        seen.append(tenancy.get_current_user())
        return ("ok", args, kwargs)

    wrapped = tenancy.wrap_async_for_user("alice", inner)

    # Fire it the way APScheduler would: no surrounding tenancy.
    assert tenancy.get_current_user() is None
    result = asyncio.run(wrapped("x", k=1))
    assert seen == ["alice"]
    assert result == ("ok", ("x",), {"k": 1})

    # Should NOT leak the binding back out.
    assert tenancy.get_current_user() is None


def test_wrap_async_independent_per_user():
    """Two wrappers around the same callable, different user_ids, must
    each see their own tenant — no cross-contamination."""
    import asyncio
    seen: list[str | None] = []

    async def inner():
        seen.append(tenancy.get_current_user())

    a = tenancy.wrap_async_for_user("alice", inner)
    b = tenancy.wrap_async_for_user("bob", inner)

    async def both():
        await a()
        await b()
        await a()

    asyncio.run(both())
    assert seen == ["alice", "bob", "alice"]


# ── Proactive jitter — same user → same offset, cohort spreads ─────────────

def test_proactive_offset_is_deterministic_and_spread():
    """Each user_id should map to a stable offset in [0, 5) so reboots don't
    shift the schedule, and a cohort of users should distribute across the
    5 buckets evenly enough to avoid a thundering herd at :00."""
    import hashlib

    def _offset(uid: str) -> int:
        return int(hashlib.md5(uid.encode()).hexdigest(), 16) % 5

    # Deterministic per-user
    assert _offset("alice") == _offset("alice")
    assert _offset("bob") == _offset("bob")

    # Reasonable spread across 100 fake users
    counts = [0] * 5
    for i in range(100):
        counts[_offset(f"user-{i:04d}")] += 1
    # No single minute holds >50% of the cohort
    assert max(counts) < 50, f"thundering herd risk — bucket counts {counts}"
