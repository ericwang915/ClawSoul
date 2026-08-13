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
    """Isolate HERANDHIM_HOME so tests never touch a real install."""
    home = tempfile.mkdtemp()
    monkeypatch.setenv("HERANDHIM_HOME", home)
    from herandhim import config
    monkeypatch.setattr(config, "_HERANDHIM_BASE", __import__("pathlib").Path(home))
    yield home


# ── Humanized delivery ────────────────────────────────────────────────────


def test_reply_is_split_into_message_bubbles():
    """A multi-paragraph reply must go out as separate messages, not one blob."""
    from herandhim.core.humanize import split_burst
    parts = split_burst("hey you\n\nguess what happened\n\nthe demo worked 😂")
    assert len(parts) == 3


def test_burst_never_exceeds_three_bubbles():
    from herandhim.core.humanize import split_burst
    assert len(split_burst("\n\n".join(f"line {i}" for i in range(9)))) <= 3


def test_single_paragraph_stays_one_message():
    from herandhim.core.humanize import split_burst
    assert split_burst("just one line") == ["just one line"]


def test_photos_almost_always_get_a_reaction_and_logistics_never_do():
    from herandhim.core.humanize import pick_reaction
    assert any(pick_reaction(True, "") for _ in range(20)), "a photo should draw a reaction"
    assert not any(pick_reaction(False, "ok sounds good") for _ in range(20)), \
        "ordinary logistics should not"


def test_absence_is_phrased_in_human_units():
    from herandhim.core.humanize import humanize_gap
    assert humanize_gap(45) == "about 45 minutes"
    assert humanize_gap(200) == "about 3 hours"
    assert humanize_gap(60 * 24 * 2) == "about 2 days"


# ── Personal dates ────────────────────────────────────────────────────────


def test_recurring_date_fires_on_its_anniversary_not_its_original_year():
    from herandhim.core.personal_dates import PersonalDates
    pd = PersonalDates()
    pd.add("1996-03-03", "their birthday", recurring=True)
    assert [d.label for d in pd.today_hits(today=date(2031, 3, 3))] == ["their birthday"]
    assert pd.today_hits(today=date(2031, 3, 4)) == []


def test_one_off_date_disappears_after_it_passes():
    from herandhim.core.personal_dates import PersonalDates
    pd = PersonalDates()
    pd.add("2030-07-02", "job interview", recurring=False)
    assert pd.upcoming(days=7, today=date(2030, 6, 30))
    assert pd.upcoming(days=7, today=date(2030, 7, 10)) == []


def test_a_date_today_takes_over_the_proactive_message():
    """A birthday must not be left to the probability roll — it drives content."""
    from herandhim.core.personal_dates import PersonalDates
    from herandhim.scheduler.proactive import _build_prompt, _todays_personal_dates

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
    from herandhim.core.humanize import open_thread
    thread = open_thread(_FakeAgent([
        {"topic": "greeting", "sentiment": "neutral", "intensity": 0.1,
         "context_summary": "said hi"},
        {"topic": "work", "sentiment": "negative", "intensity": 0.8,
         "context_summary": "argued with his boss about the deadline"},
    ]))
    assert "argued with his boss" in thread, "must carry the real content"
    assert "work" in thread


def test_follow_up_is_empty_when_there_is_nothing_worth_raising():
    from herandhim.core.humanize import open_thread
    assert open_thread(_FakeAgent([])) == ""
    assert open_thread(_FakeAgent([
        {"topic": "general", "sentiment": "neutral", "intensity": 0.1,
         "context_summary": "chatted"},
    ])) == ""


def test_follow_up_reaches_the_proactive_prompt():
    """Regression: open_thread was exported but never called anywhere."""
    from herandhim.scheduler.proactive import _build_prompt
    agent = _FakeAgent([
        {"topic": "work", "sentiment": "negative", "intensity": 0.9,
         "context_summary": "argued with his boss about the deadline"},
    ])
    prompt = _build_prompt(datetime.now(), {"sentiment": "negative"}, agent=agent)
    assert "argued with his boss" in prompt


# ── The sulk ledger ───────────────────────────────────────────────────────


def test_unanswered_messages_accumulate_then_reset_when_the_user_replies():
    from herandhim.core import proactive_state as ps
    sid = "telegram:test"
    ps.clear(sid)
    for _ in range(3):
        ps.record_sent(sid)
    assert ps.unanswered(sid) == 3
    ps.clear(sid)
    assert ps.unanswered(sid) == 0


def test_sulk_ledger_is_per_conversation():
    from herandhim.core import proactive_state as ps
    ps.clear("telegram:a"); ps.clear("telegram:b")
    ps.record_sent("telegram:a")
    assert ps.unanswered("telegram:b") == 0


# ── Wiring guards: the exact class of bug that escaped the suite ──────────


def test_proactive_shares_the_users_session():
    """A separate session meant she had no memory of her own messages."""
    import inspect
    from herandhim.scheduler import proactive
    src = inspect.getsource(proactive)
    assert "_chat_session_id" in src
    assert 'session_id = "proactive:main"' not in src


def test_proactive_generation_preserves_her_message_in_history():
    """chat() would persist the synthetic instruction as if the user sent it."""
    import inspect
    from herandhim.scheduler.proactive import _proactive_chat

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
        __import__("herandhim.scheduler.proactive", fromlist=["x"]))


def test_telegram_path_wires_up_humanized_delivery():
    """Guards against the delivery helpers going unused again."""
    import inspect
    from herandhim.channels import telegram_bot
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
    from herandhim.core.image_gen import generator as G

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
    from herandhim.core.image_gen import generator as G

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
    from herandhim.core.image_gen import generator as G

    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **kw: _fake_response({"images": [{"url": "https://x/f.png"}]}))
    ref = tmp_path / "face.jpg"
    ref.write_bytes(b"\xff\xd8\xfffake")
    gen = G.SeedreamGenerator(api_key="k", provider="fal",
                              base_url="https://fal.run", model="fal-ai/flux/schnell")
    assert gen.generate("selfie", reference_image=str(ref))


def test_image_guard_runs_before_any_backend_is_called(monkeypatch):
    """The content chokepoint must not be bypassable by switching provider."""
    from herandhim.core.image_gen import generator as G

    called = {"n": 0}
    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **kw: (called.__setitem__("n", called["n"] + 1),
                                          _fake_response({"data": []}))[1])
    monkeypatch.setattr("herandhim.core.image_gen.guard.assert_allowed",
                        lambda p: (_ for _ in ()).throw(
                            __import__("herandhim.core.image_gen.guard",
                                       fromlist=["x"]).ImageBlocked("nope")))
    gen = G.SeedreamGenerator(api_key="k", provider="seedream",
                              base_url="http://x", model="m")
    with pytest.raises(G.SeedreamError):
        gen.generate("blocked prompt")
    assert called["n"] == 0, "guard must fire before the network call"


# ── Image backends: async + local protocols ───────────────────────────────


def _resp(payload=None, *, content=b"", ctype="application/json"):
    class R:
        ok, status_code = True, 200
        headers = {"Content-Type": ctype}

        def __init__(self):
            self.content = content

        def json(self):
            if payload is None:
                raise ValueError("no json")
            return payload

        def raise_for_status(self):
            pass
    return R()


def _gen(monkeypatch, provider, **over):
    from herandhim.core.image_gen import generator as G
    monkeypatch.setattr(G.time, "sleep", lambda *a: None)  # don't wait in tests
    env, base, model, _ref = G.PROVIDERS[provider]
    return G.SeedreamGenerator(api_key=over.pop("key", "k"), provider=provider,
                               base_url=over.pop("base", base) or "http://x",
                               model=over.pop("model", model) or "m")


def test_openrouter_unwraps_the_data_url_it_answers_with(monkeypatch):
    """It returns images inline as data URLs; callers must see plain base64."""
    from herandhim.core.image_gen import generator as G
    seen = {}
    monkeypatch.setattr(G.requests, "post", lambda url, **kw: (
        seen.update(url=url, body=kw.get("json")),
        _resp({"choices": [{"message": {"images": [
            {"image_url": {"url": "data:image/png;base64,aGk="}}]}}]}))[1])
    out = _gen(monkeypatch, "openrouter").generate("selfie")
    assert seen["url"].endswith("/chat/completions")
    assert seen["body"]["modalities"] == ["image", "text"]
    assert out == [{"b64": "aGk="}]


def test_bfl_submits_then_polls_until_the_image_is_ready(monkeypatch):
    """A job that isn't Ready yet must not be read as a finished image."""
    from herandhim.core.image_gen import generator as G
    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **kw: _resp({"id": "j1", "polling_url": "https://bfl/p"}))
    states = iter([{"status": "Pending"}, {"status": "Pending"},
                   {"status": "Ready", "result": {"sample": "https://bfl/i.jpg"}}])
    polls = {"n": 0}
    monkeypatch.setattr(G.requests, "get", lambda *a, **kw: (
        polls.__setitem__("n", polls["n"] + 1), _resp(next(states)))[1])
    assert _gen(monkeypatch, "bfl").generate("selfie") == [{"url": "https://bfl/i.jpg"}]
    assert polls["n"] == 3, "must keep polling while the job is pending"


def test_bfl_surfaces_a_failed_job_instead_of_hanging(monkeypatch):
    from herandhim.core.image_gen import generator as G
    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **kw: _resp({"id": "j", "polling_url": "https://bfl/p"}))
    monkeypatch.setattr(G.requests, "get",
                        lambda *a, **kw: _resp({"status": "Content Moderated"}))
    with pytest.raises(G.SeedreamError, match="Content Moderated"):
        _gen(monkeypatch, "bfl").generate("selfie")


def test_bfl_sends_the_reference_image_that_keeps_her_face(monkeypatch, tmp_path):
    from herandhim.core.image_gen import generator as G
    seen = {}
    monkeypatch.setattr(G.requests, "post", lambda url, **kw: (
        seen.update(body=kw.get("json")),
        _resp({"id": "j", "polling_url": "https://bfl/p"}))[1])
    monkeypatch.setattr(G.requests, "get", lambda *a, **kw: _resp(
        {"status": "Ready", "result": {"sample": "https://bfl/i.jpg"}}))
    ref = tmp_path / "face.jpg"; ref.write_bytes(b"\xff\xd8\xffface")
    _gen(monkeypatch, "bfl").generate("selfie", reference_image=str(ref))
    assert seen["body"]["input_image"], "Kontext needs the reference to preserve the subject"


def test_dashscope_polls_its_async_task(monkeypatch):
    from herandhim.core.image_gen import generator as G
    seen = {}
    monkeypatch.setattr(G.requests, "post", lambda url, **kw: (
        seen.update(url=url, headers=kw.get("headers")),
        _resp({"output": {"task_id": "t1"}}))[1])
    states = iter([{"output": {"task_status": "RUNNING"}},
                   {"output": {"task_status": "SUCCEEDED",
                               "results": [{"url": "https://ds/i.png"}]}}])
    monkeypatch.setattr(G.requests, "get", lambda url, **kw: (
        seen.update(poll=url), _resp(next(states)))[1])
    out = _gen(monkeypatch, "dashscope").generate("selfie")
    assert seen["headers"]["X-DashScope-Async"] == "enable"
    assert seen["poll"].endswith("/tasks/t1")
    assert out == [{"url": "https://ds/i.png"}]


def test_dashscope_raises_on_a_failed_task(monkeypatch):
    from herandhim.core.image_gen import generator as G
    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **kw: _resp({"output": {"task_id": "t"}}))
    monkeypatch.setattr(G.requests, "get", lambda *a, **kw: _resp(
        {"output": {"task_status": "FAILED", "message": "bad prompt"}}))
    with pytest.raises(G.SeedreamError, match="FAILED"):
        _gen(monkeypatch, "dashscope").generate("selfie")


def test_stability_asks_for_json_so_it_stays_on_the_base64_path(monkeypatch):
    from herandhim.core.image_gen import generator as G
    seen = {}
    monkeypatch.setattr(G.requests, "post", lambda url, **kw: (
        seen.update(url=url, headers=kw.get("headers")),
        _resp({"image": "aGk="}))[1])
    assert _gen(monkeypatch, "stability").generate("selfie") == [{"b64": "aGk="}]
    assert seen["url"].endswith("/v2beta/stable-image/generate/core")
    assert seen["headers"]["Accept"] == "application/json"


def test_pollinations_needs_no_key_at_all(monkeypatch):
    """The point of this backend is working before you sign up anywhere."""
    from herandhim.core.image_gen import generator as G
    seen = {}
    monkeypatch.setattr(G.requests, "get", lambda url, **kw: (
        seen.update(url=url, params=kw.get("params")),
        _resp(content=b"\x89PNG\r\n\x1a\nx", ctype="image/png"))[1])
    gen = G.SeedreamGenerator(api_key="", provider="pollinations",
                              base_url="https://image.pollinations.ai", model="flux")
    out = gen.generate("a warm selfie on a balcony")
    assert "/prompt/" in seen["url"] and "%20" in seen["url"], "prompt must be URL-encoded"
    assert out and out[0]["b64"]


def test_keyless_backends_do_not_demand_an_api_key(monkeypatch):
    from herandhim.core.image_gen import generator as G
    for name in ("sdwebui", "comfyui", "pollinations"):
        assert G._read_api_key(name) == "", f"{name} should need no key"
    with pytest.raises(G.SeedreamError, match="No API key"):
        G._read_api_key("bfl")


def test_comfyui_submits_a_graph_then_fetches_the_rendered_file(monkeypatch):
    """ComfyUI is submit → poll history → download by filename, not one call."""
    from herandhim.core.image_gen import generator as G
    seen = {}
    monkeypatch.setattr(G.requests, "post", lambda url, **kw: (
        seen.update(post=url, graph=kw.get("json", {}).get("prompt")),
        _resp({"prompt_id": "p1"}))[1])

    hist = iter([
        {},  # not finished yet
        {"p1": {"outputs": {"9": {"images": [
            {"filename": "HerAndHim_001.png", "subfolder": "", "type": "output"}]}}}},
    ])

    def fake_get(url, **kw):
        if "/history/" in url:
            return _resp(next(hist))
        seen["view"] = kw.get("params")
        return _resp(content=b"\x89PNG\r\n\x1a\nx", ctype="image/png")

    monkeypatch.setattr(G.requests, "get", fake_get)
    out = _gen(monkeypatch, "comfyui", base="http://localhost:8188").generate(
        "a warm selfie", size="832x1216", seed=42)

    assert seen["post"].endswith("/prompt")
    assert seen["view"]["filename"] == "HerAndHim_001.png"
    assert out and out[0]["b64"]

    latent = seen["graph"]["5"]["inputs"]
    assert (latent["width"], latent["height"]) == (832, 1216), "size must reach the graph"
    assert seen["graph"]["3"]["inputs"]["seed"] == 42, "seed must reach the sampler"
    assert seen["graph"]["6"]["inputs"]["text"] == "a warm selfie", "prompt placeholder unfilled"
    assert "%negative%" not in str(seen["graph"]), "placeholders must all be substituted"


def test_comfyui_runs_your_own_workflow_when_you_point_at_one(monkeypatch, tmp_path):
    """Anyone already using ComfyUI has a tuned graph; ours shouldn't override it."""
    import json
    from herandhim.core.image_gen import generator as G

    wf = tmp_path / "mine.json"
    wf.write_text(json.dumps({
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "%prompt%"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }))
    monkeypatch.setattr(G, "_cfg", lambda p, f, env="", default="":
                        str(wf) if f == "workflow" else default)

    seen = {}
    monkeypatch.setattr(G.requests, "post", lambda url, **kw: (
        seen.update(graph=kw.get("json", {}).get("prompt")), _resp({"prompt_id": "p"}))[1])
    monkeypatch.setattr(G.requests, "get", lambda url, **kw: (
        _resp({"p": {"outputs": {"2": {"images": [
            {"filename": "a.png", "subfolder": "", "type": "output"}]}}}})
        if "/history/" in url
        else _resp(content=b"\x89PNG\r\n\x1a\nx", ctype="image/png")))

    _gen(monkeypatch, "comfyui").generate("her on the balcony")
    assert set(seen["graph"]) == {"1", "2"}, "must run the user's graph, not the built-in"
    assert seen["graph"]["1"]["inputs"]["text"] == "her on the balcony"


def test_openai_size_follows_the_aspect_ratio(monkeypatch):
    """Portrait selfies shouldn't be squared off by the size mapping."""
    from herandhim.core.image_gen.generator import _openai_size
    assert _openai_size("2048x2048") == "1024x1024"
    assert _openai_size("832x1216") == "1024x1536"
    assert _openai_size("1216x832") == "1536x1024"
    assert _openai_size("garbage") == "1024x1024"


def test_every_declared_backend_is_actually_implemented():
    """A name in PROVIDERS with no handler would fall through to the OpenAI
    shape and fail confusingly at runtime."""
    from herandhim.core.image_gen.generator import PROVIDERS, SeedreamGenerator
    generic = {"seedream", "custom"}  # deliberately share _gen_openai_like
    missing = [p for p in PROVIDERS
               if p not in generic and not hasattr(SeedreamGenerator, f"_gen_{p}")]
    assert not missing, f"backends declared but not implemented: {missing}"


def test_new_backends_cannot_ship_undocumented():
    """A backend nobody can find is a backend nobody uses."""
    import json
    import pathlib
    from herandhim.core.image_gen.generator import PROVIDERS

    root = pathlib.Path(__file__).resolve().parent.parent
    env = (root / "deploy/local/.env.example").read_text()
    readme = (root / "README.md").read_text()
    skills = json.loads((root / "herandhim.example.json").read_text())["skills"]

    for name in PROVIDERS:
        assert name in skills, f"{name} missing from herandhim.example.json"
        assert f"`{name}`" in readme, f"{name} missing from README.md"
        assert name in env or f"HERANDHIM_{name.upper()}" in env, \
            f"{name} missing from .env.example"
