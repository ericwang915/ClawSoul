"""Verify single-tenant → multi-tenant migration."""

from __future__ import annotations

from pathlib import Path

from claw_soul import migrate as mig


def _seed_legacy(base: Path) -> None:
    (base / "claw_soul.json").write_text('{"llm":{"provider":"deepseek"}}')
    (base / "daemon.log").write_text("hello")
    ctx = base / "context"
    ctx.mkdir()
    (ctx / "soul").mkdir()
    (ctx / "soul" / "SOUL.md").write_text("# soul")
    (ctx / "memory").mkdir()
    (ctx / "memory" / "MEMORY.md").write_text("# mem")


def test_migrate_moves_legacy_entries(tmp_path):
    _seed_legacy(tmp_path)

    summary = mig.migrate("alice-uuid", base=tmp_path)

    target = tmp_path / "users" / "alice-uuid"
    assert (target / "claw_soul.json").read_text() == '{"llm":{"provider":"deepseek"}}'
    assert (target / "daemon.log").read_text() == "hello"
    assert (target / "context" / "soul" / "SOUL.md").read_text() == "# soul"
    assert not (tmp_path / "claw_soul.json").exists(), "source should be gone after move"
    assert "claw_soul.json" in summary["moved"]
    assert "context" in summary["moved"]


def test_migrate_is_idempotent(tmp_path):
    _seed_legacy(tmp_path)
    mig.migrate("alice-uuid", base=tmp_path)
    second = mig.migrate("alice-uuid", base=tmp_path)
    # Second run: source already gone, target already has data → all skipped
    assert second["moved"] == []
    # All entries either missing at source or already at target
    assert set(second["skipped_already_exists"] + second["skipped_missing"]) == set(
        mig.LEGACY_ENTRIES
    )


def test_migrate_leaves_other_users_dirs_alone(tmp_path):
    _seed_legacy(tmp_path)
    bob = tmp_path / "users" / "bob-uuid"
    bob.mkdir(parents=True)
    (bob / "claw_soul.json").write_text("bob")

    mig.migrate("alice-uuid", base=tmp_path)

    assert (bob / "claw_soul.json").read_text() == "bob"
    assert (tmp_path / "users" / "alice-uuid" / "claw_soul.json").exists()


def test_migrate_dry_run_makes_no_changes(tmp_path):
    _seed_legacy(tmp_path)
    summary = mig.migrate("alice-uuid", base=tmp_path, dry_run=True)
    assert summary["dry_run"] is True
    assert (tmp_path / "claw_soul.json").exists(), "dry run must not move files"
    assert not (tmp_path / "users" / "alice-uuid" / "claw_soul.json").exists()


def test_migrate_skips_when_target_already_has_entry(tmp_path):
    """If a partial migration happened, don't clobber the user's data."""
    _seed_legacy(tmp_path)
    # Pretend a previous migration moved claw_soul.json but crashed before context
    user_dir = tmp_path / "users" / "alice-uuid"
    user_dir.mkdir(parents=True)
    (user_dir / "claw_soul.json").write_text("already migrated")

    mig.migrate("alice-uuid", base=tmp_path)

    assert (user_dir / "claw_soul.json").read_text() == "already migrated"
    # Source still in place because target existed
    assert (tmp_path / "claw_soul.json").exists()
    # context should have moved (target didn't have it yet)
    assert (user_dir / "context" / "soul" / "SOUL.md").exists()
