"""
Behavioural regression tests for the companion's delivery layer.

These exist because four realism features (shared proactive session,
personal-date preemption, specific follow-ups, and photo memory) were silently
removed during a refactor and the entire suite still passed. Unit tests covered
the helpers; nothing checked that they were actually *wired up*. Each test here
pins a behaviour a user would notice losing.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def _tenant_home(monkeypatch):
    """Isolate CLAWSOUL_HOME so tests never touch a real install."""
    home = tempfile.mkdtemp()
    monkeypatch.setenv("CLAWSOUL_HOME", home)
    from claw_soul import config
    monkeypatch.setattr(config, "_CLAWSOUL_BASE", __import__("pathlib").Path(home))
    yield home


# ── Humanized delivery ────────────────────────────────────────────────────


def test_reply_is_split_into_message_bubbles():
    """A multi-paragraph reply must go out as separate messages, not one blob."""
    from claw_soul.core.humanize import split_burst
    parts = split_burst("hey you\n\nguess what happened\n\nthe demo worked 😂")
    assert len(parts) == 3


def test_burst_never_exceeds_three_bubbles():
    from claw_soul.core.humanize import split_burst
    assert len(split_burst("\n\n".join(f"line {i}" for i in range(9)))) <= 3


def test_single_paragraph_stays_one_message():
    from claw_soul.core.humanize import split_burst
    assert split_burst("just one line") == ["just one line"]


def test_photos_almost_always_get_a_reaction_and_logistics_never_do():
    from claw_soul.core.humanize import pick_reaction
    assert any(pick_reaction(True, "") for _ in range(20)), "a photo should draw a reaction"
    assert not any(pick_reaction(False, "ok sounds good") for _ in range(20)), \
        "ordinary logistics should not"


def test_absence_is_phrased_in_human_units():
    from claw_soul.core.humanize import humanize_gap
    assert humanize_gap(45) == "about 45 minutes"
    assert humanize_gap(200) == "about 3 hours"
    assert humanize_gap(60 * 24 * 2) == "about 2 days"


# ── Personal dates ────────────────────────────────────────────────────────


def test_recurring_date_fires_on_its_anniversary_not_its_original_year():
    from claw_soul.core.personal_dates import PersonalDates
    pd = PersonalDates()
    pd.add("1996-03-03", "their birthday", recurring=True)
    assert [d.label for d in pd.today_hits(today=date(2031, 3, 3))] == ["their birthday"]
    assert pd.today_hits(today=date(2031, 3, 4)) == []


def test_one_off_date_disappears_after_it_passes():
    from claw_soul.core.personal_dates import PersonalDates
    pd = PersonalDates()
    pd.add("2030-07-02", "job interview", recurring=False)
    assert pd.upcoming(days=7, today=date(2030, 6, 30))
    assert pd.upcoming(days=7, today=date(2030, 7, 10)) == []


def test_a_date_today_takes_over_the_proactive_message():
    """A birthday must not be left to the probability roll — it drives content."""
    from claw_soul.core.personal_dates import PersonalDates
    from claw_soul.scheduler.proactive import _build_prompt, _todays_personal_dates

    PersonalDates().add(date.today().isoformat(), "their birthday", recurring=True)
    assert [d.label for d in _todays_personal_dates()] == ["their birthday"]

    prompt = _build_prompt(datetime.now(), {"sentiment": "neutral"})
    assert "birthday" in prompt or "生日" in prompt
    assert "generic" in prompt or "普通的问候" in prompt


# ── Follow-ups: the actual thing, not just a topic label ──────────────────


class _FakeAgent:
    """Minimal agent exposing the affect graph the follow-up picker reads."""

    def __init__(self, events):
        graph = type("G", (), {"get_recent": lambda self, days=2: events})()
        self.memory = type("M", (), {"emotional_graph": graph})()


def test_follow_up_quotes_the_event_not_only_the_topic():
    from claw_soul.core.humanize import open_thread
    thread = open_thread(_FakeAgent([
        {"topic": "greeting", "sentiment": "neutral", "intensity": 0.1,
         "context_summary": "said hi"},
        {"topic": "work", "sentiment": "negative", "intensity": 0.8,
         "context_summary": "argued with his boss about the deadline"},
    ]))
    assert "argued with his boss" in thread, "must carry the real content"
    assert "work" in thread


def test_follow_up_is_empty_when_there_is_nothing_worth_raising():
    from claw_soul.core.humanize import open_thread
    assert open_thread(_FakeAgent([])) == ""
    assert open_thread(_FakeAgent([
        {"topic": "general", "sentiment": "neutral", "intensity": 0.1,
         "context_summary": "chatted"},
    ])) == ""


def test_follow_up_reaches_the_proactive_prompt():
    """Regression: open_thread was exported but never called anywhere."""
    from claw_soul.scheduler.proactive import _build_prompt
    agent = _FakeAgent([
        {"topic": "work", "sentiment": "negative", "intensity": 0.9,
         "context_summary": "argued with his boss about the deadline"},
    ])
    prompt = _build_prompt(datetime.now(), {"sentiment": "negative"}, agent=agent)
    assert "argued with his boss" in prompt


# ── The sulk ledger ───────────────────────────────────────────────────────


def test_unanswered_messages_accumulate_then_reset_when_the_user_replies():
    from claw_soul.core import proactive_state as ps
    sid = "telegram:test"
    ps.clear(sid)
    for _ in range(3):
        ps.record_sent(sid)
    assert ps.unanswered(sid) == 3
    ps.clear(sid)
    assert ps.unanswered(sid) == 0


def test_sulk_ledger_is_per_conversation():
    from claw_soul.core import proactive_state as ps
    ps.clear("telegram:a"); ps.clear("telegram:b")
    ps.record_sent("telegram:a")
    assert ps.unanswered("telegram:b") == 0


# ── Wiring guards: the exact class of bug that escaped the suite ──────────


def test_proactive_shares_the_users_session():
    """A separate session meant she had no memory of her own messages."""
    import inspect
    from claw_soul.scheduler import proactive
    src = inspect.getsource(proactive)
    assert "_chat_session_id" in src
    assert 'session_id = "proactive:main"' not in src


def test_proactive_generation_preserves_her_message_in_history():
    """chat() would persist the synthetic instruction as if the user sent it."""
    import inspect
    from claw_soul.scheduler.proactive import _proactive_chat

    calls = {}

    class A:
        def chat_proactive(self, p):
            calls["proactive"] = p
            return "hi"

        def chat(self, p):
            calls["plain"] = p
            return "hi"

    _proactive_chat(A(), "it's morning")
    assert "proactive" in calls and "plain" not in calls
    assert "_proactive_chat" in inspect.getsource(
        __import__("claw_soul.scheduler.proactive", fromlist=["x"]))


def test_telegram_path_wires_up_humanized_delivery():
    """Guards against the delivery helpers going unused again."""
    import inspect
    from claw_soul.channels import telegram_bot
    src = inspect.getsource(telegram_bot)
    for hook in ("send_burst", "maybe_react", "reply_delay",
                 "_remember_their_photo", "_ignored_count"):
        assert hook in src, f"{hook} is not wired into the Telegram path"


# ── Image backends ────────────────────────────────────────────────────────


def _fake_response(payload):
    class R:
        ok, status_code = True, 200
        headers = {"Content-Type": "image/png"}
        content = b"\x89PNG\r\n\x1a\nfake"

        def json(self):
            return payload

        def raise_for_status(self):
            pass
    return R()


@pytest.mark.parametrize("provider,endpoint,payload", [
    ("seedream",  "/images/generations", {"data": [{"url": "https://x/i.jpg"}]}),
    ("openai",    "/images/edits",       {"data": [{"b64_json": "aGk="}]}),
    ("gemini",    ":generateContent",
     {"candidates": [{"content": {"parts": [{"inlineData": {"data": "aGk="}}]}}]}),
    ("fal",       "fal-ai",              {"images": [{"url": "https://x/f.png"}]}),
    ("replicate", "/predictions",        {"output": ["https://x/r.png"]}),
    ("sdwebui",   "/sdapi/v1/txt2img",   {"images": ["aGk="]}),
])
def test_every_image_backend_hits_its_own_endpoint(monkeypatch, tmp_path,
                                                   provider, endpoint, payload):
    """Each backend speaks a different protocol; a shared shape would break them."""
    from claw_soul.core.image_gen import generator as G

    seen = {}

    def fake_post(url, headers=None, timeout=None, **kw):
        seen["url"] = url
        return _fake_response(payload)

    monkeypatch.setattr(G.requests, "post", fake_post)
    ref = tmp_path / "face.jpg"
    ref.write_bytes(b"\xff\xd8\xfffake")

    gen = G.SeedreamGenerator(api_key="k", provider=provider,
                              base_url=G.PROVIDERS[provider][1] or "http://x",
                              model=G.PROVIDERS[provider][2] or "m")
    out = gen.generate("a warm selfie", reference_image=str(ref))
    assert endpoint in seen["url"]
    assert out and (out[0].get("url") or out[0].get("b64"))


def test_openai_uses_the_edit_endpoint_only_when_anchoring_a_face(monkeypatch, tmp_path):
    """Face consistency depends on the reference reaching /images/edits."""
    from claw_soul.core.image_gen import generator as G

    seen = {}
    monkeypatch.setattr(G.requests, "post",
                        lambda url, **kw: (seen.__setitem__("url", url),
                                           _fake_response({"data": [{"b64_json": "aGk="}]}))[1])
    gen = G.SeedreamGenerator(api_key="k", provider="openai",
                              base_url="https://api.openai.com/v1", model="gpt-image-1")

    gen.generate("selfie")
    assert seen["url"].endswith("/images/generations")

    ref = tmp_path / "face.jpg"
    ref.write_bytes(b"\xff\xd8\xfffake")
    gen.generate("selfie", reference_image=str(ref))
    assert seen["url"].endswith("/images/edits")


def test_backends_without_reference_support_still_generate(monkeypatch, tmp_path):
    """A backend that can't anchor a face should degrade, not raise."""
    from claw_soul.core.image_gen import generator as G

    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **kw: _fake_response({"images": [{"url": "https://x/f.png"}]}))
    ref = tmp_path / "face.jpg"
    ref.write_bytes(b"\xff\xd8\xfffake")
    gen = G.SeedreamGenerator(api_key="k", provider="fal",
                              base_url="https://fal.run", model="fal-ai/flux/schnell")
    assert gen.generate("selfie", reference_image=str(ref))


def test_image_guard_runs_before_any_backend_is_called(monkeypatch):
    """The content chokepoint must not be bypassable by switching provider."""
    from claw_soul.core.image_gen import generator as G

    called = {"n": 0}
    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **kw: (called.__setitem__("n", called["n"] + 1),
                                          _fake_response({"data": []}))[1])
    monkeypatch.setattr("claw_soul.core.image_gen.guard.assert_allowed",
                        lambda p: (_ for _ in ()).throw(
                            __import__("claw_soul.core.image_gen.guard",
                                       fromlist=["x"]).ImageBlocked("nope")))
    gen = G.SeedreamGenerator(api_key="k", provider="seedream",
                              base_url="http://x", model="m")
    with pytest.raises(G.SeedreamError):
        gen.generate("blocked prompt")
    assert called["n"] == 0, "guard must fire before the network call"
