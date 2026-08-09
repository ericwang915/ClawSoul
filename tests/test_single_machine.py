"""
Guards for the "it's your machine" promise.

This project was a hosted SaaS before it was self-hosted, and the conversion
left enforcement behind that the test suite happily ignored: a subscription
message cap that cut chat off mid-conversation and told the user to upgrade,
a pricing modal in the shipped dashboard, panels wired to a Postgres that no
longer exists, and a login page whose only purpose was to load a script from
a CDN. Every test here pins something a self-hoster would be right to be
angry about.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWSOUL_HOME", str(tmp_path))
    from claw_soul import config
    monkeypatch.setattr(config, "_CLAWSOUL_BASE", tmp_path)
    config._configs.clear()
    config._config_paths.clear()
    from claw_soul.core import quota
    quota.reset_for_tests()
    yield


# ── No subscription enforcement ───────────────────────────────────────────


def test_chat_is_never_capped_by_default():
    """You pay for your own API calls. Nothing may cut the conversation off."""
    from claw_soul.core import quota
    for _ in range(5000):
        quota.record_message()
    assert quota.check_messages() is None
    assert quota.message_status()["unlimited"] is True


def test_photos_are_never_capped_by_default():
    from claw_soul.core import quota
    for _ in range(500):
        quota.record_photo()
    assert quota.check_photos() is None


def test_disk_is_unlimited_until_you_ask_for_a_cap():
    """The old default silently killed selfies after ~285 photos."""
    from claw_soul.core import quota
    assert quota.check_disk(extra_bytes=50 * 1024**3) is None
    assert quota.disk_status()["unlimited"] is True


def test_a_cap_you_configure_yourself_is_honoured(monkeypatch):
    """Opt-in caps still work — a household or small VPS may want one."""
    from claw_soul import config
    from claw_soul.core import quota
    monkeypatch.setattr(config, "get_int",
                        lambda *k, default=0: 3 if k[-1] == "dailyMessages" else default)
    for _ in range(3):
        quota.record_message()
    refusal = quota.check_messages()
    assert refusal is not None
    assert "upgrade" not in refusal.lower(), "a self-host has nothing to upgrade to"
    assert "tomorrow" in refusal.lower()


def test_no_refusal_anywhere_tells_the_user_to_pay():
    from claw_soul.core import quota
    src = pathlib.Path(quota.__file__).read_text()
    for word in ("upgrade", "subscription", "Pro", "Ultra", "tier"):
        assert word not in src, f"quota.py still mentions {word!r}"


# ── The dashboard is not a storefront ─────────────────────────────────────


def test_dashboard_ships_no_pricing_or_account_ui():
    html = (ROOT / "claw_soul/web/static/index.html").read_text()
    for banned in ("Go Premium", "pricing-modal", "openPricing", "/api/plans",
                   "Launch offer", "Choose your plan", "Sign out",
                   "/api/auth/", "supabase"):
        assert banned not in html, f"dashboard still contains {banned!r}"


def test_nothing_in_the_app_calls_a_third_party_at_runtime():
    """A 'zero cloud' install must not phone home. The deleted login page
    pulled supabase-js from a CDN — the only such call in the product."""
    for path in (ROOT / "claw_soul/web/static").rglob("*.html"):
        html = path.read_text()
        for cdn in ("cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com",
                    "herandhim.ai"):
            assert cdn not in html, f"{path.name} loads {cdn}"


def test_the_dead_login_page_is_gone():
    assert not (ROOT / "claw_soul/web/static/login.html").exists()
    assert not (ROOT / "claw_soul/web/auth.py").exists()


# ── Panels show real data ─────────────────────────────────────────────────


def test_bonding_panel_reflects_actual_conversation():
    """It read Postgres, so it rendered Level 1 / 0 messages forever while
    the real history sat in SQLite on the same disk."""
    from claw_soul.core.storage import StorageManager
    from claw_soul.web import sanctum_api

    StorageManager.reset_for_tests()
    store = StorageManager.instance()
    for day in ("2026-08-05", "2026-08-06", "2026-08-07"):
        for i in range(5):
            store.index_turn("web:main", "user", f"hi {day} {i}",
                             ts=f"{day}T10:0{i}:00+00:00")

    assert sanctum_api._fetch_turn_count() == 15
    assert sanctum_api._fetch_active_days() == 3
    assert sanctum_api._fetch_last_message_at() is not None


def test_timeline_surfaces_locally_logged_milestones():
    from claw_soul.core.storage import StorageManager
    from claw_soul.web import sanctum_api

    StorageManager.reset_for_tests()
    StorageManager.instance().log_event("milestone", {"title": "One month"})
    kinds = [e["kind"] for e in
             sanctum_api._fetch_events(kinds=["milestone", "bonding_level"])]
    assert "milestone" in kinds


# ── Language correctness ──────────────────────────────────────────────────


def test_city_blurb_never_leaks_chinese_into_a_non_chinese_persona():
    """The old fallback returned a hardcoded Chinese sentence for every
    unseeded city — including English, Korean and Spanish personas."""
    from claw_soul.onboard import city_background
    for country, region in [("US", "Austin"), ("KR", "Seoul"), ("DE", "Berlin")]:
        blurb = city_background(country, region)
        assert not any("一" <= ch <= "鿿" for ch in blurb), \
            f"{country}/{region} produced Chinese text: {blurb!r}"


# ── Nothing points at infrastructure that no longer exists ────────────────


def test_no_module_still_reaches_for_the_old_cloud():
    banned = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET",
              "ROUTER_PUBLIC_URL", "rest/v1/", "user_machines")
    offenders = []
    for path in (ROOT / "claw_soul").rglob("*.py"):
        src = path.read_text()
        for token in banned:
            if token in src:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert not offenders, "\n".join(offenders)


def test_setup_docs_do_not_ask_for_credentials_that_do_nothing():
    """Following the old .env.example produced a bricked install."""
    env = (ROOT / "deploy/local/.env.example").read_text()
    for token in ("SUPABASE_", "ALLOWED_EMAILS", "CLAW_DEV_NO_AUTH"):
        assert token not in env, f".env.example still documents {token}"


def test_declared_license_matches_the_classifier():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "AGPL-3.0-only" in pyproject
    assert "MIT License" not in pyproject, "classifier contradicts the license"


def test_required_deps_carry_nothing_only_the_saas_needed():
    pyproject = (ROOT / "pyproject.toml").read_text()
    required = pyproject.split("[project.optional-dependencies]")[0]
    for dead in ("pyjwt", "boto3"):
        assert dead not in required, f"{dead} is still a required dependency"


def test_example_config_has_no_dead_keys():
    cfg = json.loads((ROOT / "claw_soul.example.json").read_text())
    assert "plans" not in cfg
    assert "supabase" not in json.dumps(cfg).lower()
