"""Verify the web-facing companion wizard helpers work per-tenant."""

from __future__ import annotations

import json

import pytest

from claw_soul import companion, config
from claw_soul.core import tenancy


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_CLAWSOUL_BASE", tmp_path)
    config._configs.clear()
    config._config_paths.clear()


_CHOICES = {
    "userName": "Eric",
    "userGender": "male",
    "userAge": "26-35",
    "companionName": "Aria",
    "companionGender": "female",
    "companionAge": "26-35",
    "archetype": "healer",
    "dynamic": "romance",
    "tone": "sweet",
    "proactivity": "attentive",
    "stress": "listen",
    "deepTalk": "emotions",
}


def test_options_payload_is_self_describing():
    # Every option in OPTIONS has key / label / description and at least 2 choices
    for field, opts in companion.OPTIONS.items():
        assert len(opts) >= 2
        for opt in opts:
            assert set(opt.keys()) == {"key", "label", "description"}


def test_validate_passes_well_formed_choices():
    cleaned = companion.validate(_CHOICES)
    assert cleaned["userName"] == "Eric"
    assert cleaned["companionName"] == "Aria"
    assert cleaned["archetype"] == "healer"


def test_validate_rejects_unknown_archetype():
    bad = {**_CHOICES, "archetype": "evil-overlord"}
    with pytest.raises(companion.ChoiceError):
        companion.validate(bad)


def test_validate_applies_defaults_for_blank_names():
    cleaned = companion.validate({**_CHOICES, "userName": "", "companionName": "   "})
    assert cleaned["userName"] == "主人"
    assert cleaned["companionName"] == "小爪"


def test_apply_choices_writes_files_to_active_tenant(tmp_path):
    with tenancy.user_context("alice"):
        companion.apply_choices(_CHOICES)

    alice_ctx = tmp_path / "users" / "alice" / "context"
    assert (alice_ctx / "soul" / "SOUL.md").exists()
    assert (alice_ctx / "persona").exists()
    assert (alice_ctx / "profile").exists()

    # Choices persisted to alice's config
    cfg_path = tmp_path / "users" / "alice" / "claw_soul.json"
    assert cfg_path.exists()
    saved = json.loads(cfg_path.read_text())
    assert saved["companion"]["archetype"] == "healer"


def test_apply_choices_is_tenant_isolated(tmp_path):
    with tenancy.user_context("alice"):
        companion.apply_choices({**_CHOICES, "archetype": "healer"})
    with tenancy.user_context("bob"):
        companion.apply_choices({**_CHOICES, "archetype": "witty"})

    alice_cfg = json.loads((tmp_path / "users/alice/claw_soul.json").read_text())
    bob_cfg = json.loads((tmp_path / "users/bob/claw_soul.json").read_text())
    assert alice_cfg["companion"]["archetype"] == "healer"
    assert bob_cfg["companion"]["archetype"] == "witty"

    # Files are separate too
    alice_soul = (tmp_path / "users/alice/context/soul/SOUL.md").read_text()
    bob_soul = (tmp_path / "users/bob/context/soul/SOUL.md").read_text()
    assert alice_soul != bob_soul  # different archetypes produce different files


def test_load_choices_returns_none_before_setup(tmp_path):
    with tenancy.user_context("nobody"):
        assert companion.load_choices() in (None, {})


def test_load_choices_round_trip(tmp_path):
    with tenancy.user_context("alice"):
        companion.apply_choices(_CHOICES)
        loaded = companion.load_choices()
        assert loaded is not None
        assert loaded["companionName"] == "Aria"
