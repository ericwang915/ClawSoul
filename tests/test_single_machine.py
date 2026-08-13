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
    monkeypatch.setenv("HERANDHIM_HOME", str(tmp_path))
    from herandhim import config
    monkeypatch.setattr(config, "_HERANDHIM_BASE", tmp_path)
    config._configs.clear()
    config._config_paths.clear()
    from herandhim.core import quota
    quota.reset_for_tests()
    yield


# ── No subscription enforcement ───────────────────────────────────────────


def test_chat_is_never_capped_by_default():
    """You pay for your own API calls. Nothing may cut the conversation off."""
    from herandhim.core import quota
    for _ in range(5000):
        quota.record_message()
    assert quota.check_messages() is None
    assert quota.message_status()["unlimited"] is True


def test_photos_are_never_capped_by_default():
    from herandhim.core import quota
    for _ in range(500):
        quota.record_photo()
    assert quota.check_photos() is None


def test_disk_is_unlimited_until_you_ask_for_a_cap():
    """The old default silently killed selfies after ~285 photos."""
    from herandhim.core import quota
    assert quota.check_disk(extra_bytes=50 * 1024**3) is None
    assert quota.disk_status()["unlimited"] is True


def test_a_cap_you_configure_yourself_is_honoured(monkeypatch):
    """Opt-in caps still work — a household or small VPS may want one."""
    from herandhim import config
    from herandhim.core import quota
    monkeypatch.setattr(config, "get_int",
                        lambda *k, default=0: 3 if k[-1] == "dailyMessages" else default)
    for _ in range(3):
        quota.record_message()
    refusal = quota.check_messages()
    assert refusal is not None
    assert "upgrade" not in refusal.lower(), "a self-host has nothing to upgrade to"
    assert "tomorrow" in refusal.lower()


def test_no_refusal_anywhere_tells_the_user_to_pay():
    from herandhim.core import quota
    src = pathlib.Path(quota.__file__).read_text()
    for word in ("upgrade", "subscription", "Pro", "Ultra", "tier"):
        assert word not in src, f"quota.py still mentions {word!r}"


# ── The dashboard is not a storefront ─────────────────────────────────────


def test_dashboard_ships_no_pricing_or_account_ui():
    html = (ROOT / "herandhim/web/static/index.html").read_text()
    for banned in ("Go Premium", "pricing-modal", "openPricing", "/api/plans",
                   "Launch offer", "Choose your plan", "Sign out",
                   "/api/auth/", "supabase"):
        assert banned not in html, f"dashboard still contains {banned!r}"


def test_nothing_in_the_app_calls_a_third_party_at_runtime():
    """A 'zero cloud' install must not phone home. The deleted login page
    pulled supabase-js from a CDN — the only such call in the product."""
    for path in (ROOT / "herandhim/web/static").rglob("*.html"):
        html = path.read_text()
        for cdn in ("cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com",
                    "herandhim.ai"):
            assert cdn not in html, f"{path.name} loads {cdn}"


def test_the_dead_login_page_is_gone():
    assert not (ROOT / "herandhim/web/static/login.html").exists()
    assert not (ROOT / "herandhim/web/auth.py").exists()


# ── Panels show real data ─────────────────────────────────────────────────


def test_bonding_panel_reflects_actual_conversation():
    """It read Postgres, so it rendered Level 1 / 0 messages forever while
    the real history sat in SQLite on the same disk."""
    from herandhim.core.storage import StorageManager
    from herandhim.web import sanctum_api

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
    from herandhim.core.storage import StorageManager
    from herandhim.web import sanctum_api

    StorageManager.reset_for_tests()
    StorageManager.instance().log_event("milestone", {"title": "One month"})
    kinds = [e["kind"] for e in
             sanctum_api._fetch_events(kinds=["milestone", "bonding_level"])]
    assert "milestone" in kinds


# ── Language correctness ──────────────────────────────────────────────────


def test_city_blurb_never_leaks_chinese_into_a_non_chinese_persona():
    """The old fallback returned a hardcoded Chinese sentence for every
    unseeded city — including English, Korean and Spanish personas."""
    from herandhim.onboard import city_background
    for country, region in [("US", "Austin"), ("KR", "Seoul"), ("DE", "Berlin")]:
        blurb = city_background(country, region)
        assert not any("一" <= ch <= "鿿" for ch in blurb), \
            f"{country}/{region} produced Chinese text: {blurb!r}"


# ── Nothing points at infrastructure that no longer exists ────────────────


def test_no_module_still_reaches_for_the_old_cloud():
    banned = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET",
              "ROUTER_PUBLIC_URL", "rest/v1/", "user_machines")
    offenders = []
    for path in (ROOT / "herandhim").rglob("*.py"):
        src = path.read_text()
        for token in banned:
            if token in src:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert not offenders, "\n".join(offenders)


def test_setup_docs_do_not_ask_for_credentials_that_do_nothing():
    """Following the old .env.example produced a bricked install."""
    env = (ROOT / "deploy/local/.env.example").read_text()
    for token in ("SUPABASE_", "ALLOWED_EMAILS", "HERANDHIM_DEV_NO_AUTH"):
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
    cfg = json.loads((ROOT / "herandhim.example.json").read_text())
    assert "plans" not in cfg
    assert "supabase" not in json.dumps(cfg).lower()


# ── Nothing ships that can't run ──────────────────────────────────────────


def test_every_registered_endpoint_actually_imports():
    """/api/marketplace/* and /api/skillhub/* 500'd on every call for months:
    they imported core.skillhub, a module deleted long ago. A route that
    can't execute is worse than a missing one."""
    import re
    app_src = (ROOT / "herandhim/web/app.py").read_text()
    for mod in re.findall(r"from \.\.core import (\w+)", app_src):
        assert (ROOT / f"herandhim/core/{mod}.py").exists() or \
               (ROOT / f"herandhim/core/{mod}").is_dir(), \
            f"web/app.py imports core.{mod}, which does not exist"


def test_dashboard_has_no_javascript_that_can_never_run():
    """Superseded panel code accumulated here — refreshSanctum* was replaced
    by refreshToday* and left behind, referencing DOM that never existed."""
    import re
    html = (ROOT / "herandhim/web/static/index.html").read_text()
    funcs = set(re.findall(r"function\s+([\w$]+)\s*\(", html))
    uncalled = sorted(f for f in funcs
                      if len(re.findall(r"\b" + re.escape(f) + r"\b", html)) <= 1)
    assert not uncalled, f"defined but never called: {uncalled}"


def test_editing_her_identity_by_hand_is_reachable_and_works():
    """The modal was fully built but nothing opened it, and the save path
    500'd on a global that no longer existed — while still writing the file,
    so it reported failure after succeeding."""
    import re
    html = (ROOT / "herandhim/web/static/index.html").read_text()
    for doc in ("soul", "persona", "tools", "index"):
        assert f"openIdentityEditor('{doc}')" in html, f"no way to open the {doc} editor"

    app_src = (ROOT / "herandhim/web/app.py").read_text()
    reload_fn = re.search(r"def _reload_agent_identity.*?(?=\n\n\n)", app_src, re.S).group(0)
    assert "global _agent\n" not in reload_fn, "still references the removed _agent global"


def test_retrieval_still_works_without_the_optional_search_extra():
    """scikit-learn pulls in scipy (~130 MB). It's a middle-tier fallback, so
    the base install must not need it."""
    import builtins
    import importlib
    real = builtins.__import__

    def blocked(name, *a, **k):
        if name.split(".")[0] in ("sklearn", "scipy", "sentence_transformers"):
            raise ImportError("simulated: not installed")
        return real(name, *a, **k)

    builtins.__import__ = blocked
    try:
        from herandhim.core.retrieval import dense
        importlib.reload(dense)
        r = dense.EmbeddingRetriever()
        assert r.backend_name == "bigram-jaccard"
        r.fit([{"content": "she loves rainy mornings"},
               {"content": "his boss moved the deadline again"}])
        hits = r.retrieve("the deadline moved", 1)
        assert hits and "deadline" in hits[0][1]["content"]
    finally:
        builtins.__import__ = real
        from herandhim.core.retrieval import dense as d2
        importlib.reload(d2)


def test_heavy_extras_are_not_required_dependencies():
    pyproject = (ROOT / "pyproject.toml").read_text()
    required = pyproject.split("[project.optional-dependencies]")[0]
    assert "scikit-learn" not in required, "scikit-learn must stay optional"
    assert "scikit-learn" in pyproject, "...but still offered as an extra"


def test_dockerfile_copies_only_paths_that_exist():
    """deploy/fly/ was renamed to deploy/docker/ but the Dockerfile kept the
    old path, so the published image never built — and the one-line quickstart
    in the README worked for nobody. CI only catches this on a release tag."""
    import re
    df = (ROOT / "deploy/docker/Dockerfile").read_text()
    missing = []
    for line in df.splitlines():
        m = re.match(r"\s*COPY\s+(.*)$", line)
        if not m:
            continue
        parts = [p for p in m.group(1).split() if not p.startswith("--")]
        for src in parts[:-1]:
            if src.startswith("/"):     # copied from an earlier build stage
                continue
            if not (ROOT / src).exists():
                missing.append(src)
    assert not missing, f"Dockerfile COPYs paths that don't exist: {missing}"


def test_readmes_quickstart_image_matches_what_ci_publishes():
    """The headline command must name the image the workflow actually pushes.

    The workflow templates the owner (``${{ github.repository_owner }}``), so
    compare the image name only — that's the part a typo would break.
    """
    import re
    wf = (ROOT / ".github/workflows/docker-publish.yml").read_text()
    # the owner is a ${{ ... }} template containing spaces — match the tail
    published = set(re.findall(r"images:.*?ghcr\.io/.*/([\w.-]+)\s*$", wf, re.M))
    assert published, "workflow declares no ghcr.io image"
    for readme in ("README.md", "README.zh-CN.md"):
        pulled = set(re.findall(r"ghcr\.io/[\w.-]+/([\w.-]+)",
                                (ROOT / readme).read_text()))
        unknown = pulled - published
        assert not unknown, f"{readme} tells users to pull {unknown}, which CI never pushes"


def test_readme_llm_table_covers_every_provider_in_the_registry():
    """The 'Supported LLMs' table sat at 6 rows while the code supported 16 —
    a reader comparing companions would count six and move on."""
    import re
    from herandhim.main import _OPENAI_COMPATIBLE
    readme = (ROOT / "README.md").read_text()
    table = re.search(r"## 🧠 Supported LLMs\n.*?(?=\n---)", readme, re.S).group(0)
    providers = set(_OPENAI_COMPATIBLE) | {"claude", "gemini"}
    missing = [p for p in providers
               if f"HERANDHIM_{p.upper()}_API_KEY" not in table
               and p not in ("ollama", "lmstudio", "custom")]
    for local in ("ollama", "lmstudio"):
        assert local in table.lower().replace(" ", ""), f"{local} missing from the table"
    assert not missing, f"providers missing from the README table: {missing}"


def test_presence_reads_storage_timestamps_in_the_right_timezone():
    """StorageManager stamps turns with naive local time; the presence badge
    read them as UTC, so on any UTC+N machine she showed 'online' for N hours
    after the user left (seconds_since was negative)."""
    from datetime import datetime, timezone

    from herandhim.core.storage import StorageManager
    from herandhim.web import sanctum_api

    StorageManager.reset_for_tests()
    StorageManager.instance().index_turn("web:main", "user", "hi")  # default ts: naive local now
    last = sanctum_api._fetch_last_message_at()
    gap = (datetime.now(timezone.utc) - last).total_seconds()
    assert 0 <= gap < 60, f"a just-sent message reads as {gap:.0f}s ago"


def test_cli_onboard_has_a_two_minute_express_path(monkeypatch):
    """`pipx install` users hit a ~30-question wizard with no skip path,
    while the web wizard has had Quick Start all along. Express must stay
    under 10 prompts and still produce a valid persisted persona."""
    import builtins
    import io
    import json
    from unittest import mock

    from herandhim import onboard

    answers = iter(["", "Eric", "", "1", "", "5", "", ""])
    prompts = []

    def fake_input(prompt=""):
        prompts.append(str(prompt))
        return next(answers, "")

    with mock.patch.object(builtins, "input", fake_input), \
         mock.patch.object(onboard.getpass, "getpass",
                           lambda *a: (prompts.append("key"), "sk-test")[1]), \
         mock.patch.object(onboard, "_validate_key", lambda *a, **k: True), \
         mock.patch("sys.stdout", io.StringIO()):
        path = onboard.run_onboard(None)

    assert len(prompts) <= 10, f"express asked {len(prompts)} questions"
    cfg = json.loads(path.read_text())
    c = cfg["companion"]
    assert c["userName"] == "Eric"
    assert c["companionGender"] in ("female", "male")
    assert c["archetype"], "personality defaults must be filled"
    assert cfg["llm"][cfg["llm"]["provider"]]["apiKey"] == "sk-test"
