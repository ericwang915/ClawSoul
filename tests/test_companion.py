"""Verify the web-facing companion wizard helpers."""

from __future__ import annotations

import json

import pytest

from claw_soul import companion, config


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
    """Blank names should fall back to language-appropriate defaults."""
    # Chinese user → Chinese defaults
    cleaned = companion.validate({**_CHOICES, "userLanguage": "zh-CN",
                                  "userName": "", "companionName": "   "})
    assert cleaned["userName"] == "主人"
    assert cleaned["companionName"] == "小爪"

    # English user (or no language preference) → English defaults
    cleaned = companion.validate({**_CHOICES, "userName": "", "companionName": "   "})
    assert cleaned["userName"] == "You"
    assert cleaned["companionName"] == "Claw"


def test_apply_choices_writes_the_identity_files(tmp_path):
    """The persona pipeline reads these off disk — setup must materialize them."""
    companion.apply_choices(_CHOICES)

    ctx = tmp_path / "context"
    assert (ctx / "soul" / "SOUL.md").exists()
    assert (ctx / "persona").exists()
    assert (ctx / "profile").exists()

    saved = json.loads((tmp_path / "claw_soul.json").read_text())
    assert saved["companion"]["archetype"] == "healer"


def test_reapplying_choices_rewrites_the_identity(tmp_path):
    """Changing the archetype must actually change who she is on disk."""
    companion.apply_choices({**_CHOICES, "archetype": "healer"})
    healer = (tmp_path / "context/soul/SOUL.md").read_text()

    companion.apply_choices({**_CHOICES, "archetype": "witty"})
    witty = (tmp_path / "context/soul/SOUL.md").read_text()

    assert healer != witty
    cfg = json.loads((tmp_path / "claw_soul.json").read_text())
    assert cfg["companion"]["archetype"] == "witty"


def test_load_choices_returns_none_before_setup(tmp_path):
    assert companion.load_choices() in (None, {})


def test_load_choices_round_trip(tmp_path):
    companion.apply_choices(_CHOICES)
    loaded = companion.load_choices()
    assert loaded is not None
    assert loaded["companionName"] == "Aria"
