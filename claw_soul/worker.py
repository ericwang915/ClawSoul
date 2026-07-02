"""
Per-user worker mode.

Launched when ``CLAW_USER_ID`` is set in the environment (see
``deploy/fly/entrypoint.sh``).  Each Fly machine pins to exactly one
tenant; the router service in :mod:`claw_soul.router` sends events here
via ``POST /dispatch``.

What it does:
  - Set the tenancy contextvar to ``CLAW_USER_ID`` at startup so every
    config / storage / memory call honours the per-tenant home dir.
  - Build one :class:`PersistentAgent` for this user and reuse it.
  - Hold a single Telegram bot Application *without polling* — we use
    ``Application.bot`` only for outbound sends (replies, photos).
    Inbound updates arrive via /dispatch from the router.
  - Track activity and exit cleanly after ``CLAW_IDLE_EXIT_SEC`` of no
    work so Fly Proxy can suspend us (free tier) or so we get a clean
    restart (paid tier).

Event kinds accepted at /dispatch:

    telegram_update    payload.update = the raw Telegram update dict
    proactive_tick     fires the ProactiveMessenger logic
    planner_tick       regenerates today's plan
    selfie_tick        fires a scheduled selfie
    wake               no-op; just touches last_active

All handlers are best-effort; the worker logs and returns 200 even on
internal errors so the router doesn't retry (Telegram's "exactly once"
semantics + idempotent agents already cover us).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import random
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .core import tenancy
from .core.persistent_agent import PersistentAgent
from .core.storage_pg import make_session_store
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


# Time-to-idle-exit defaults — tier-aware so the free tier suspends
# aggressively (no proactive ticks keep them awake anyway) while paid
# users get a longer grace window in case the user comes back mid-stream.
_IDLE_DEFAULTS_BY_TIER: dict[str, int] = {
    "free":       180,    # 3 min — wake on incoming TG webhook
    "pro":        600,    # 10 min — proactive ticks keep it warm
    "ultra":      600,
    "paid":       600,    # legacy alias for pro
    "enterprise": 0,      # 0 == never auto-exit
}


def _user_id() -> str:
    uid = os.environ.get("CLAW_USER_ID", "").strip()
    if not uid:
        raise RuntimeError("CLAW_USER_ID env var required for worker mode")
    return uid


def _tier() -> str:
    return os.environ.get("CLAW_TIER", "free").strip().lower()


def _idle_exit_sec() -> int:
    """Return the configured idle-exit window in seconds.

    Resolution order:
      1. CLAW_IDLE_EXIT_SEC env var (explicit override)
      2. Tier-aware default
      3. 180 s fallback
    """
    explicit = os.environ.get("CLAW_IDLE_EXIT_SEC", "").strip()
    if explicit:
        try:
            return int(explicit)
        except ValueError:
            pass
    return _IDLE_DEFAULTS_BY_TIER.get(_tier(), 180)


def _proactive_enabled() -> bool:
    """Free tier is reactive-only — no proactive / planner / selfie ticks."""
    from .core import plans
    return plans.proactive_enabled(_tier())


# ── App factory ────────────────────────────────────────────────────────


def create_worker_app() -> FastAPI:
    app = FastAPI(title="ClawSoul Worker", docs_url=None, redoc_url=None)

    user_id = _user_id()
    state = _WorkerState(user_id=user_id, tier=_tier())

    @app.on_event("startup")
    async def _startup() -> None:
        # Pin the tenancy contextvar for the lifetime of this process.
        tenancy.set_current_user(user_id)
        logger.info("[worker] booted for user=%s tier=%s idle_exit=%ds",
                    user_id[:8], _tier(), _idle_exit_sec())
        state.touch()
        # Hydrate persona files from the canonical Postgres store so the
        # Agent's persona loader (which reads from /data/context/*.md)
        # picks them up.  Skipped silently when the user hasn't done
        # the web wizard yet — _handle_telegram_update will refuse to
        # chat until then.
        _hydrate_persona_from_pg(user_id)
        # Idle watchdog
        asyncio.create_task(_idle_watchdog(state))

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "ok": True, "service": "clawsoul-worker",
            "user_id": user_id[:8], "tier": state.tier,
            "idle_for_sec": int(time.monotonic() - state.last_active),
        })

    @app.post("/reload")
    async def reload() -> JSONResponse:
        """Re-hydrate persona from Postgres and drop the cached agent so
        the next message rebuilds it with the new identity files.

        Called by the router (which proxies the dashboard's
        /api/setup/companion save) so wizard edits take effect without
        the user having to wait for an idle-restart.
        """
        tenancy.set_current_user(user_id)
        try:
            _hydrate_persona_from_pg(user_id)
            async with state._lock:  # noqa: SLF001
                state._agent = None  # noqa: SLF001 — force rebuild next chat
                state._sm = None     # noqa: SLF001
            logger.info("[worker] reloaded persona from Pg")
            return JSONResponse({"ok": True})
        except Exception as exc:
            logger.warning("[worker] reload failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)[:200]},
                                status_code=500)

    @app.post("/dispatch")
    async def dispatch(request: Request) -> JSONResponse:
        # ContextVars set in startup don't propagate into per-request
        # tasks that FastAPI spawns, so re-bind tenancy here on every
        # call.  Without this, config.CLAWSOUL_HOME / SessionStore /
        # memory all fall back to single-tenant paths and skip the
        # /data/users/<uid>/ directory the agent actually expects.
        tenancy.set_current_user(user_id)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON")
        kind = str((body or {}).get("kind") or "")
        payload = (body or {}).get("payload") or {}

        state.touch()
        try:
            await _handle(kind, payload, state)
        except Exception as exc:  # noqa: BLE001 — log + 200 so router moves on
            logger.exception("[worker] dispatch %s failed: %s", kind, exc)
            return JSONResponse({"ok": False, "error": str(exc)[:200]})
        return JSONResponse({"ok": True, "kind": kind})

    return app


# ── State + handlers ──────────────────────────────────────────────────


class _WorkerState:
    def __init__(self, *, user_id: str, tier: str) -> None:
        self.user_id = user_id
        self.tier = tier
        self.last_active: float = time.monotonic()
        self._lock = asyncio.Lock()
        self._agent: PersistentAgent | None = None
        self._sm: SessionManager | None = None
        self._bot_app = None  # python-telegram-bot Application, lazy
        # Telegram update_id ring buffer — guards against router retries
        # (after a cold-start dispatch took too long) re-delivering the
        # same message.  Window of 256 covers any realistic retry burst.
        self._seen_update_ids: collections.deque[int] = collections.deque(maxlen=256)

    def touch(self) -> None:
        self.last_active = time.monotonic()

    def mark_update_seen(self, update_id: int) -> bool:
        """Return True if we've already processed this update_id, in which
        case the caller should drop it.  Otherwise record it and return False."""
        if update_id in self._seen_update_ids:
            return True
        self._seen_update_ids.append(update_id)
        return False

    def sm(self) -> SessionManager:
        """The SessionManager owning per-session locks.  Only valid after
        ``get_agent()`` has run (both dispatch paths call it first)."""
        if self._sm is None:
            raise RuntimeError("session manager not initialized; call get_agent() first")
        return self._sm

    async def get_agent(self) -> PersistentAgent:
        async with self._lock:
            if self._agent is None:
                self._sm = SessionManager(
                    agent_factory=lambda sid: None,
                    store=make_session_store(),
                )
                provider = _build_provider()
                self._sm.set_factory(
                    lambda sid: PersistentAgent(
                        provider=provider,
                        store=self._sm._store,  # noqa: SLF001
                        session_id=sid,
                        verbose=False,
                    )
                )
                self._agent = self._sm.get_or_create(f"user:{self.user_id}")
            return self._agent

    async def get_bot_app(self):
        async with self._lock:
            if self._bot_app is None:
                from telegram.ext import ApplicationBuilder
                row = await _user_settings_lookup(self.user_id)
                token = (row or {}).get("telegram_bot_token") or ""
                if not token:
                    return None
                self._bot_app = ApplicationBuilder().token(token).build()
                await self._bot_app.initialize()
        return self._bot_app


# ── Dispatch handler ────────────────────────────────────────────────────


async def _handle(kind: str, payload: dict, state: _WorkerState) -> None:
    if kind == "wake":
        return  # already touched; nothing else to do

    if kind == "telegram_update":
        await _handle_telegram_update(payload.get("update") or {}, state)
        return

    # Free tier is reactive-only — router shouldn't fire these, but
    # defense in depth keeps a misconfigured tier from accidentally
    # burning compute / API on free users.
    if kind in ("proactive_tick", "planner_tick", "selfie_tick") \
            and not _proactive_enabled():
        logger.info("[worker] tier=%s rejects %s (reactive-only)",
                    _tier(), kind)
        return

    if kind in ("proactive_tick", "planner_tick", "selfie_tick"):
        # Fire-and-forget: a selfie tick runs Seedream (~30s), which would blow
        # the router's dispatch HTTP timeout and make it RETRY the tick → a
        # duplicate selfie/message (ticks have no dedup, unlike TG updates).
        # Return 200 immediately and run the handler in the background; the
        # worker stays awake (last_active was just touched).
        if kind == "proactive_tick":
            coro = _handle_proactive(state)
        elif kind == "planner_tick":
            coro = _handle_planner(state)
        else:
            coro = _handle_selfie(state, slot=payload.get("slot"))
        asyncio.create_task(_run_tick_bg(kind, coro))
        return

    raise HTTPException(status_code=400, detail=f"unknown kind: {kind!r}")


async def _run_tick_bg(kind: str, coro) -> None:
    """Await a tick handler in the background, logging (not raising) errors."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        logger.exception("[worker] %s (background) failed: %s", kind, exc)


async def _handle_telegram_update(update: dict, state: _WorkerState) -> None:
    update_id = update.get("update_id")
    if isinstance(update_id, int) and state.mark_update_seen(update_id):
        logger.info("[worker] dedup: skipping replayed update_id=%s", update_id)
        return

    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    caption = (msg.get("caption") or "").strip()
    photos = msg.get("photo") or []                       # list of PhotoSize
    doc = msg.get("document") or {}
    is_image_doc = isinstance(doc, dict) and str(doc.get("mime_type", "")).startswith("image/")
    has_image = bool(photos) or is_image_doc
    chat_id = ((msg.get("chat") or {}).get("id"))
    if (not text and not has_image) or chat_id is None:
        return  # unsupported (sticker/voice/etc.) or no chat
    user_text = text or caption                            # caption rides with a photo

    # How long since the user's PREVIOUS message (read before we bump it below),
    # so the agent can treat old feelings as old instead of as "just now".
    gap_note = ""
    try:
        from datetime import datetime, timezone
        from .router.db import get_user_machine
        _row = await get_user_machine(state.user_id)
        _prev = getattr(_row, "last_message_at", None) if _row else None
        if _prev:
            _last = datetime.fromisoformat(str(_prev).replace("Z", "+00:00"))
            _mins = (datetime.now(timezone.utc) - _last).total_seconds() / 60.0
            if _mins >= 20:
                gap_note = _humanize_gap(_mins)
    except Exception:
        gap_note = ""

    # How many of HER proactive messages they left unanswered before replying —
    # so she can be a touch sulky about being ignored, not just sweetly missing
    # them. Computed before the new turn is saved, so it counts the real streak.
    ignored_count = 0
    try:
        _pg = os.environ.get("SUPABASE_URL", "").rstrip("/")
        _pk = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if _pg and _pk:
            ignored_count = await _unanswered_proactive_count(
                state.user_id, _pg,
                {"apikey": _pk, "Authorization": f"Bearer {_pk}"},
            )
    except Exception:
        ignored_count = 0

    # Tell the scheduler we just got a fresh inbound — proactive/selfie
    # ticks within the next quiet window get suppressed so the user
    # doesn't get spammed mid-conversation.
    asyncio.create_task(_touch_message_at(state.user_id))

    # Web wizard must be completed before the bot will chat — no chat-
    # based onboarding fallback.  Direct the user to the dashboard.
    if not _has_companion_in_pg(state.user_id):
        bot_app = await state.get_bot_app()
        if bot_app is not None:
            url = os.environ.get("ROUTER_DASHBOARD_URL", "https://herandhim.ai")
            try:
                await bot_app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Welcome! 👋  Before we can chat, please finish the "
                        "quick setup on the dashboard so I know who I am and "
                        f"how to talk to you:\n\n{url}/\n\n"
                        "Come back once you're done — I'll be ready."
                    ),
                )
            except Exception as exc:
                logger.warning("[worker] setup-link send failed: %s", exc)
        return

    loop = asyncio.get_event_loop()
    agent = await state.get_agent()
    agent._gap_note = gap_note  # transient; the volatile context reads + uses it
    agent._ignored_count = ignored_count

    bot_app = await state.get_bot_app()
    if bot_app is None:
        logger.warning("[worker] no bot token configured; can't reply")
        return

    # Instant emoji reaction (photo ❤️, laughter 🤣, big feelings) — fired
    # before the LLM call, like a human seeing the message before replying.
    asyncio.create_task(_maybe_react(
        bot_app.bot, chat_id, msg.get("message_id"), has_image, user_text))

    # Inbound photo → download from Telegram and pass to the agent as a
    # multimodal turn so she actually SEES it (the provider routes image turns
    # to the vision model). Without this, photos were silently dropped.
    chat_input: object = user_text
    if has_image:
        try:
            file_id = (photos[-1].get("file_id") if photos else doc.get("file_id"))
            tg_file = await bot_app.bot.get_file(file_id)
            raw = await tg_file.download_as_bytearray()
            import base64
            mime = (doc.get("mime_type") if is_image_doc else None) or "image/jpeg"
            url = f"data:{mime};base64," + base64.b64encode(bytes(raw)).decode()
            chat_input = [
                {"type": "text",
                 "text": user_text or "（我发了一张照片给你，看看然后自然地回应～）"},
                {"type": "image_url", "image_url": {"url": url}},
            ]
        except Exception as exc:
            logger.warning("[worker] inbound photo download failed: %s", exc)
            if not user_text:
                return  # nothing to say without the image

    # First-ever conversation? Send a one-time, out-of-character AI disclaimer
    # before the companion's first words — Telegram has no chrome for the
    # standing web in-chat disclaimer. Durable signal: zero persisted turns.
    try:
        from .core.milestones import _count_turns
        if (await _count_turns(state.user_id)) == 0:
            tenancy.set_current_user(state.user_id)
            from .core import safety as _safety
            _lang0 = config.get_str("agent", "language", default="en") or "en"
            await bot_app.bot.send_message(
                chat_id=chat_id, text=_safety.first_contact_notice(_lang0))
    except Exception as exc:
        logger.warning("[worker] first-contact notice skipped: %s", exc)

    # Serialize the whole turn on the per-session lock so a proactive tick
    # or a fast second message can't interleave on the shared Agent's
    # mutable `messages` list — and so the module-global photo/file senders
    # (keyed by session_id) registered just below aren't clobbered by an
    # overlapping turn mid-flight.  See SessionManager.acquire.
    sid = agent._session_id  # noqa: SLF001
    async with state.sm().acquire(sid):
        # Register photo/file senders for the agent's session so when the LLM
        # calls take_selfie → send_photo, the bytes actually flow through
        # this user's Telegram bot.  Without this the send_photo helper
        # returns "No active channel to send through" and the agent has to
        # apologise to the user.
        #
        # photo_sent_flag tracks "did a photo go out this turn?" so we can
        # suppress the LLM's redundant tail text on TG.  captions_sent keeps
        # the actual caption that shipped — used after the turn to overwrite
        # whatever rambling text the agent saved to Pg, so web Chronicles
        # shows the same caption instead of the LLM's tail narrative.
        photo_sent_flag: list[bool] = []
        captions_sent: list[str] = []
        _register_channel_senders(sid, bot_app, chat_id, loop,
                                  photo_sent_flag, captions_sent)

        # Show "typing…" while the agent thinks.  Telegram dismisses the
        # indicator after ~5s, so refresh on a 4s interval until the agent
        # task completes.  We also need a typing tick BEFORE the LLM call
        # so the user sees it immediately.
        async def _typing_loop() -> None:
            while True:
                try:
                    await bot_app.bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception:
                    return
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(_typing_loop())
        try:
            # asyncio contextvars don't propagate into run_in_executor threads,
            # so storage_pg / config.CLAWSOUL_HOME would lose the bound user_id
            # and bail with "storage_pg called without a bound tenant".  Bind
            # tenancy inside the worker thread before calling agent.chat.
            pinned_uid = state.user_id
            def _chat_in_thread():
                tenancy.set_current_user(pinned_uid)
                return agent.chat(chat_input)
            reply = await loop.run_in_executor(None, _chat_in_thread)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                pass

        # If a photo went out THIS turn, the caption already shipped with it
        # via take_selfie / candid_shot.  Skip the LLM's trailing narrative
        # text — DeepSeek tends to bolt on "told you it would work!" /
        # "满意了？" after a successful photo even when the prompt says not to.
        if photo_sent_flag:
            # Pg already has the LLM's rambling tail text saved to the turns
            # table; overwrite it with the actual caption that shipped on TG
            # so web Chronicles shows the same text the user saw on TG.
            if captions_sent:
                asyncio.create_task(_overwrite_last_assistant_turn(
                    user_id=state.user_id,
                    session_id=sid,
                    new_content="\n\n".join(c for c in captions_sent if c),
                ))
            return

        if not reply:
            return
        # Humanize pacing: real people don't fire back instantly + uniformly.
        # Scale a short delay with reply length + jitter (the typing indicator
        # covers it), longer if it's her sleep hours. Capped so it never lags.
        try:
            await asyncio.sleep(_reply_delay(reply))
        except Exception:
            pass
        if not await _send_burst(bot_app.bot, chat_id, reply):
            return

    # Outbound succeeded — touch last_message_at, mark onboarded once
    # bot_name lands in memory, and emit any milestones that just
    # tripped (first chat, day-streak, message-count, bonding level up).
    # All three are background tasks so they never block the reply.
    asyncio.create_task(_touch_message_at(state.user_id))
    asyncio.create_task(_maybe_mark_onboarded(state.user_id, agent))
    asyncio.create_task(_emit_milestones(state.user_id))


# ── Proactive throttling ──────────────────────────────────────────────
# Router cron fires every 30 min. Without these gates, the worker would
# send a guaranteed message each tick (~48/day) — way more than the
# 4–6 the original ProactiveMessenger was designed for.

# Minimum spacing between two proactive messages, regardless of
# probability rolls. Without this, two ticks 30 min apart could both
# fire and the user sees a wall of check-ins.
_MIN_PROACTIVE_GAP_MIN = 180

# Hard daily cap. Old default was 6 per ProactiveMessenger.maxDaily;
# 2 keeps it firmly on the "thoughtful partner", not "needy bot", side.
_DEFAULT_MAX_PROACTIVE_DAILY = 2

# Stop initiating once this many proactive messages have gone unanswered since
# the user's last real reply — don't pile on someone who's busy / not in the
# mood.  The streak (and this pause) clears the moment they message back.
_PROACTIVE_UNANSWERED_LIMIT = 3

# Quiet hours in persona-local time (companion's home tz, not user's).
# Defaults match the original ProactiveMessenger range.
_PROACTIVE_QUIET_START_HOUR = 23   # 23:00 inclusive
_PROACTIVE_QUIET_END_HOUR   = 8    # 08:00 exclusive

# Per-tick probability so even "in window" ticks aren't auto-fire —
# spreads sends out across the active day rather than packing them into the
# first few ticks after the quiet window ends.  Weighted by the user's local
# hour: loneliness peaks in the evening, so a proactive check-in lands with
# more value then (HBS 24-078 — companions help most when they reach you at a
# lonely moment).  Daytime ticks are rarer so we don't feel needy at work.
_PROACTIVE_TICK_PROB = 0.40


def _proactive_prob_for_hour(hour: int) -> float:
    """Probability weight by user-local hour (quiet hours are blocked
    separately).  Evening/night-ish gets the highest weight."""
    if 19 <= hour <= 22:        # prime evening — wind-down, most alone
        return 0.45
    if hour in (8, 9, 18):      # morning hello / after-work
        return 0.28
    if 10 <= hour <= 17:        # working hours — lighter touch
        return 0.12
    return 0.22                 # edges


def _user_local_now() -> datetime:
    """Now in the *user's* IANA timezone — the real human, not the persona.

    Quiet hours mean "don't ping me when I'm asleep", which is the
    user's sleep schedule.  Falls back to the persona's tz (better
    than UTC if the wizard predates the ``user.timezone`` field) and
    finally to naive ``datetime.now()`` if nothing's configured.
    """
    tz_name = ""
    try:
        from . import config as _cfg
        tz_name = (
            _cfg.get_str("user", "timezone", default="")
            or _cfg.get_str("persona", "timezone", default="")
            or ""
        )
    except Exception:
        tz_name = ""
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now()


def _in_proactive_quiet_hours(now_local: datetime) -> bool:
    h = now_local.hour
    start = _PROACTIVE_QUIET_START_HOUR
    end = _PROACTIVE_QUIET_END_HOUR
    if start <= end:
        return start <= h < end
    return h >= start or h < end


async def _unanswered_proactive_count(user_id: str, pg_url: str, headers: dict) -> int:
    """How many proactive messages we've sent since the user's last real reply.

    'Last real reply' = the latest ``turns`` row with role=user (proactive
    sends are not stored as user turns, so they don't reset this).  Returns 0
    on any lookup failure — we'd rather not silently mute the bot on a transient
    Pg hiccup.
    """
    import httpx
    # Latest genuine inbound user message timestamp.
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{pg_url}/rest/v1/turns",
                params={"user_id": f"eq.{user_id}", "role": "eq.user",
                        "select": "ts", "order": "ts.desc", "limit": "1"},
                headers=headers,
            )
        rows = r.json() if r.is_success else []
    except Exception as exc:  # noqa: BLE001
        logger.debug("[worker] last-user-turn lookup failed: %s", exc)
        return 0
    last_user_ts = rows[0]["ts"] if rows else None

    params = {"user_id": f"eq.{user_id}", "kind": "eq.proactive_sent", "select": "id"}
    if last_user_ts:                       # else: never replied → count them all
        params["ts"] = f"gt.{last_user_ts}"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{pg_url}/rest/v1/events", params=params,
                headers={**headers, "Prefer": "count=exact", "Range": "0-0"},
            )
        cr = r.headers.get("content-range") or ""
        if "/" in cr:
            return int(cr.rsplit("/", 1)[1])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[worker] unanswered-count lookup failed: %s", exc)
    return 0


async def _proactive_throttle_decision(user_id: str) -> tuple[bool, str]:
    """Decide whether to send a proactive message now.

    Returns ``(should_send, reason_if_not)``.  Best-effort: any Pg
    lookup failure logs and falls through to "send" rather than
    silently muting the bot — we'd rather over-message a little than
    have the proactive feature mysteriously go dark.
    """
    import os as _os
    import random as _random

    import httpx
    now_local = _user_local_now()
    if _in_proactive_quiet_hours(now_local):
        return False, f"quiet-hours ({now_local.strftime('%H:%M')} local)"

    pg_url = _os.environ.get("SUPABASE_URL", "").rstrip("/")
    pg_key = _os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if pg_url and pg_key:
        from datetime import timedelta, timezone
        now_utc = datetime.now(timezone.utc)
        headers = {"apikey": pg_key, "Authorization": f"Bearer {pg_key}"}

        # Unanswered-streak check: if we've already sent N proactive messages
        # since their last real reply, hold off — they're busy or not feeling
        # it, and stacking more reads as needy.  Clears when they message back.
        unanswered = await _unanswered_proactive_count(user_id, pg_url, headers)
        if unanswered >= _PROACTIVE_UNANSWERED_LIMIT:
            return False, f"unanswered-streak ({unanswered} since last reply)"

        # Min-gap check
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    f"{pg_url}/rest/v1/events",
                    params={
                        "user_id": f"eq.{user_id}",
                        "kind":    "eq.proactive_sent",
                        "select":  "ts",
                        "order":   "ts.desc",
                        "limit":   "1",
                    },
                    headers=headers,
                )
            rows = r.json() if r.is_success else []
            if rows:
                last_ts = rows[0].get("ts") or ""
                try:
                    last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    gap = (now_utc - last).total_seconds() / 60
                    if gap < _MIN_PROACTIVE_GAP_MIN:
                        return False, f"min-gap ({gap:.0f} min since last send)"
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[worker] proactive last-sent lookup failed: %s", exc)

        # Daily-cap check (count in last 24h)
        try:
            cutoff = (now_utc - timedelta(hours=24)).isoformat()
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    f"{pg_url}/rest/v1/events",
                    params={
                        "user_id": f"eq.{user_id}",
                        "kind":    "eq.proactive_sent",
                        "ts":      f"gte.{cutoff}",
                        "select":  "id",
                    },
                    headers={**headers, "Prefer": "count=exact", "Range": "0-0"},
                )
            content_range = r.headers.get("content-range") or ""
            if "/" in content_range:
                try:
                    sent_today = int(content_range.rsplit("/", 1)[1])
                    if sent_today >= _DEFAULT_MAX_PROACTIVE_DAILY:
                        return False, f"daily-cap ({sent_today}/{_DEFAULT_MAX_PROACTIVE_DAILY})"
                except ValueError:
                    pass
        except Exception as exc:
            logger.debug("[worker] proactive daily-count lookup failed: %s", exc)

    # A personal date landing TODAY (their birthday, an interview) skips the
    # probability roll — missing it to a dice roll is exactly the robotic
    # failure this system exists to prevent. Quiet hours / min-gap / daily-cap
    # above still apply, so it fires at the first eligible morning tick.
    try:
        from .core.personal_dates import PersonalDates
        if PersonalDates().today_hits(today=now_local.date()):
            return True, ""
    except Exception:
        pass

    # Final probability roll, weighted toward the user's lonelier evening
    # hours, so we don't predictably fire 90 min after the previous message
    # and we show up more when it lands with the most value.
    prob = _proactive_prob_for_hour(now_local.hour)
    if _random.random() > prob:
        return False, f"probability-roll (p={prob:.2f} @ {now_local.hour}h)"

    return True, ""


async def _log_proactive_sent(user_id: str) -> None:
    """Record a ``proactive_sent`` event for throttling later.  Best-effort."""
    import os as _os

    import httpx
    pg_url = _os.environ.get("SUPABASE_URL", "").rstrip("/")
    pg_key = _os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not pg_url or not pg_key:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            await c.post(
                f"{pg_url}/rest/v1/events",
                json={"user_id": user_id, "kind": "proactive_sent", "payload": {}},
                headers={
                    "apikey":        pg_key,
                    "Authorization": f"Bearer {pg_key}",
                    "Content-Type":  "application/json",
                    "Prefer":        "return=minimal",
                },
            )
    except Exception as exc:
        logger.debug("[worker] proactive event log failed: %s", exc)


async def _handle_proactive(state: _WorkerState) -> None:
    """Proactive ticks land here.  Gates on quiet-hours / min-gap /
    daily-cap / probability roll before generating, then logs an event
    so the next tick can see what happened.
    """
    from .scheduler.proactive import _build_prompt

    # Self-heal the daily plan if it's missing/stale (e.g. the 00:01 planner
    # tick was missed because the worker was suspended). Runs on every proactive
    # tick but is a cheap no-op once the plan is fresh for today.
    try:
        from .scheduler.planner import ensure_fresh_plan
        await ensure_fresh_plan(_build_provider())
    except Exception as exc:
        logger.debug("[worker] plan self-heal skipped: %s", exc)

    row = await _user_settings_lookup(state.user_id)
    chat_id = (row or {}).get("telegram_chat_id")
    if chat_id is None:
        return

    should_send, reason = await _proactive_throttle_decision(state.user_id)
    if not should_send:
        logger.info("[worker] proactive skipped for %s: %s",
                    state.user_id[:8], reason)
        return

    loop = asyncio.get_event_loop()
    agent = await state.get_agent()
    sid = agent._session_id  # noqa: SLF001
    sm = state.sm()
    # If a user turn is being processed right now, drop this proactive tick
    # rather than queueing behind it: a check-in that lands the instant the
    # user just spoke is noise, and we must never interleave on the shared
    # Agent's history.
    if sm.get_lock(sid).locked():
        logger.info("[worker] proactive dropped for %s (turn in progress)",
                    state.user_id[:8])
        return
    # Build the prompt against the user's local clock, not the container's
    # UTC, so the persona's sense of time-of-day matches the human's.
    prompt = _build_prompt(_user_local_now())
    # Follow-up fidelity: surface the most emotionally-significant thing from
    # the last 2 days WITH its actual content, so she can ask "did the thing
    # with your boss blow over?" instead of a generic check-in. (The affect
    # analyzer already extracts topic + summary per turn; production just
    # never fed it back until now.)
    thread = _open_thread(agent)
    if thread:
        prompt = (
            f"Recent thread you remember — {thread}. If it fits naturally, "
            "follow up on THIS specifically (reference the actual thing, not "
            "just the topic); if they seemed upset about it, lead with care. "
            "Skip it only if your message is about something time-sensitive.\n\n"
            + prompt
        )
    # If one of THEIR dates is today (birthday, interview…), that IS the
    # message — it preempts generic check-in content.
    try:
        from .core.personal_dates import PersonalDates
        hits = PersonalDates().today_hits(today=_user_local_now().date())
        if hits:
            labels = "; ".join(h.label for h in hits[:2])
            prompt = (
                f"IMPORTANT — today is: {labels}. Your message should be about "
                "THIS, in your own voice (a birthday gets a real, personal "
                "celebration from a partner; an interview/exam gets a warm "
                "good-luck). Skip generic check-in content.\n\n" + prompt
            )
    except Exception:
        pass
    pinned_uid = state.user_id
    agent._gap_note = ""  # proactive has no "current user message" to anchor to
    agent._ignored_count = 0
    def _chat_proactive():
        tenancy.set_current_user(pinned_uid)
        # chat_proactive() does NOT persist the synthetic prompt as a user
        # turn — so it won't leak into the web Chronicles as a fake user msg.
        t = agent.chat_proactive(prompt)
        return t
    async with sm.acquire(sid):
        text = await loop.run_in_executor(None, _chat_proactive)
    if not text:
        return
    bot_app = await state.get_bot_app()
    if bot_app:
        if not await _send_burst(bot_app.bot, int(chat_id), text):
            return
        # Record AFTER the TG send succeeds so a failed delivery doesn't
        # eat a slot — next tick can retry with the same throttle budget.
        await _log_proactive_sent(state.user_id)


async def _handle_planner(state: _WorkerState) -> None:
    """Daily planner — regenerate today_plan.md for this user."""
    from .scheduler.planner import generate_daily_plan
    # generate_daily_plan() reads tenancy-scoped paths internally
    provider = _build_provider()
    await generate_daily_plan(provider)


async def _handle_selfie(state: _WorkerState, slot: str | None = None) -> None:
    from .core.image_gen import take_selfie
    from .core.image_gen.selfie import is_enabled
    if not is_enabled():
        return
    loop = asyncio.get_event_loop()
    pinned_uid = state.user_id
    agent = await state.get_agent()
    def _gen_selfie():
        tenancy.set_current_user(pinned_uid)
        res = take_selfie()
        # In-character caption (not the raw plan slot, which leaks the schedule
        # format + meta actions like "sent a message").
        cap = agent.caption_for_selfie(res.scene.activity or "") if agent else ""
        return res, cap
    try:
        result, caption = await loop.run_in_executor(None, _gen_selfie)
    except Exception as exc:
        logger.warning("[worker] selfie gen failed: %s", exc)
        return
    row = await _user_settings_lookup(state.user_id)
    chat_id = (row or {}).get("telegram_chat_id")
    if chat_id is None:
        return
    bot_app = await state.get_bot_app()
    if bot_app:
        with open(result.path, "rb") as f:
            await bot_app.bot.send_photo(
                chat_id=int(chat_id), photo=f,
                caption=(caption or None),
            )


# ── Idle watchdog ──────────────────────────────────────────────────────


async def _idle_watchdog(state: _WorkerState) -> None:
    """Exit the process when idle for too long, letting Fly suspend us.

    Tier behaviour:
      - free        : ~3 min idle → exit (no proactive ticks waking us)
      - paid        : ~10 min idle → exit (proactive 5-min ticks usually
                      keep us alive in practice)
      - enterprise  : never auto-exit (idle_exit_sec == 0)
    """
    threshold = _idle_exit_sec()
    if threshold <= 0:
        logger.info("[worker] auto-exit disabled (tier=%s)", _tier())
        return

    loop = asyncio.get_event_loop()
    last_backup = time.monotonic()
    while True:
        await asyncio.sleep(30)
        now = time.monotonic()
        # Periodic memory checkpoint (~5 min) so an abrupt kill — a deploy that
        # resets the rootfs, a crash — loses at most a few minutes of memory,
        # not the whole session. Best-effort, off the event loop.
        if now - last_backup >= 300:
            last_backup = now
            try:
                from .core import memory_backup
                await loop.run_in_executor(None, memory_backup.backup, state.user_id)
            except Exception as exc:
                logger.debug("[worker] periodic memory backup skipped: %s", exc)
        idle = now - state.last_active
        if idle >= threshold:
            logger.info("[worker] idle for %.0fs ≥ %ds (tier=%s), exiting",
                        idle, threshold, _tier())
            # Checkpoint long-term memory to Tigris before we go — so it
            # survives a later machine destroy / migration, not just suspend.
            try:
                from .core import memory_backup
                memory_backup.backup(state.user_id)
            except Exception as exc:
                logger.warning("[worker] memory backup on exit failed: %s", exc)
            # SIGTERM-equivalent: clean exit, Fly Proxy will mark stopped.
            os._exit(0)


# ── Helpers ────────────────────────────────────────────────────────────


def _build_provider():
    """Build the LLM provider from the per-tenant claw_soul.json — same
    code path as the legacy daemon, just lifted out so worker mode can
    re-use it without dragging in the full main.py."""
    from .main import _build_provider as _b
    return _b()


async def _user_settings_lookup(user_id: str) -> dict | None:
    """Look up this user's settings row directly (worker has the service
    role key in env via Fly secrets)."""
    from .router.db import get_user_setting_row
    try:
        return await get_user_setting_row(user_id)
    except Exception as exc:
        logger.warning("[worker] settings lookup failed: %s", exc)
        return None


def _open_thread(agent) -> str:
    """The most emotionally-significant recent event (last 2 days) with its
    actual content — '{topic}: \"{summary}\"' — or '' if nothing qualifies.

    Prefers negative / high-intensity events (the ones a caring partner would
    circle back on) and skips filler topics.
    """
    try:
        events = agent.memory.emotional_graph.get_recent(days=2)
    except Exception:
        return ""
    cands = [
        e for e in events
        if (e.get("context_summary") or "").strip()
        and (e.get("topic") or "").lower() not in ("general", "greeting", "")
    ]
    if not cands:
        return ""
    def _score(e: dict) -> float:
        s = float(e.get("intensity", 0) or 0)
        if e.get("sentiment") == "negative":
            s += 0.5
        return s
    best = max(cands, key=_score)
    return f"{best.get('topic')}: \"{best.get('context_summary')}\""


def _reply_delay(text: str) -> float:
    """A short, human-feeling pause before sending — scales with length + jitter,
    longer during her local sleep hours, hard-capped so it never feels laggy."""
    n = len(text or "")
    base = 0.7 + min(n, 360) * 0.011          # ~0.7s + up to ~4s for long replies
    delay = base * random.uniform(0.6, 1.5)
    try:
        from .core import tenancy
        if 1 <= tenancy.now_in_bot_tz().hour < 7:
            delay += random.uniform(2.0, 6.0)  # groggy / slow at her night
    except Exception:
        pass
    return min(delay, 9.0)


# Telegram only allows a fixed emoji set for reactions; these are all legal.
_REACT_PHOTO = ["❤️", "😍", "🥰", "🔥"]
_REACT_FUNNY = ["🤣", "😁"]
_REACT_SAD   = ["😢", "🤗"]
_REACT_LOVE  = ["😘", "❤️"]
_REACT_WIN   = ["🎉", "🏆", "👏"]


def _pick_reaction(has_image: bool, text: str) -> str | None:
    """A human reacts to SOME messages — a photo almost always, a big feeling
    sometimes, ordinary logistics never. Returns an emoji or None."""
    t = (text or "").lower()
    if has_image and random.random() < 0.85:
        return random.choice(_REACT_PHOTO)
    if any(k in t for k in ("哈哈", "笑死", "😂", "🤣", "lmao", "lol", "haha")):
        return random.choice(_REACT_FUNNY) if random.random() < 0.5 else None
    if any(k in t for k in ("想你", "爱你", "miss you", "love you", "爱死")):
        return random.choice(_REACT_LOVE) if random.random() < 0.6 else None
    if any(k in t for k in ("难过", "哭了", "累死", "崩溃", "😭", "sad", "awful", "terrible")):
        return random.choice(_REACT_SAD) if random.random() < 0.5 else None
    if any(k in t for k in ("通过了", "成功", "拿到", "offer", "升职", "过了", "passed", "got the job", "nailed")):
        return random.choice(_REACT_WIN) if random.random() < 0.7 else None
    return None


async def _maybe_react(bot, chat_id, message_id, has_image: bool, text: str) -> None:
    """Best-effort emoji reaction on the user's message — the instant
    acknowledgment a real texter gives before typing a reply."""
    emoji = _pick_reaction(has_image, text)
    if not emoji or message_id is None:
        return
    try:
        from telegram import ReactionTypeEmoji
        await bot.set_message_reaction(
            chat_id=chat_id, message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji)],
        )
    except Exception as exc:
        logger.debug("[worker] reaction skipped: %s", exc)


def _split_burst(text: str, max_parts: int = 3) -> list[str]:
    """Split a reply into 1–3 message bubbles on blank lines, like a real
    texter firing off consecutive messages.

    The persona prompt already asks her to write in short paragraphs
    separated by blank lines — those ARE the natural bubble boundaries.
    Merges down to ``max_parts`` by joining the shortest neighbours, so we
    never chop mid-thought. Single-paragraph replies pass through as one
    message. Each part respects Telegram's 4096-char limit.
    """
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) <= 1:
        return [text[:4096]]
    while len(paras) > max_parts:
        i = min(range(len(paras) - 1),
                key=lambda j: len(paras[j]) + len(paras[j + 1]))
        paras[i:i + 2] = [paras[i] + "\n\n" + paras[i + 1]]
    return [p[:4096] for p in paras]


async def _send_burst(bot, chat_id, text: str) -> bool:
    """Send a reply as 1–3 consecutive bubbles with human typing gaps.

    Between bubbles: a typing action + a short pause scaled to the length of
    the UPCOMING bubble (you type before you send), so the rhythm reads like
    a person, not a queue flush. Returns True if at least one bubble sent.
    """
    parts = _split_burst(text)
    sent = False
    for i, part in enumerate(parts):
        if i > 0:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            gap = 0.8 + min(len(part), 200) * 0.012 * random.uniform(0.6, 1.4)
            await asyncio.sleep(min(gap, 3.5))
        try:
            await bot.send_message(chat_id=chat_id, text=part)
            sent = True
        except Exception as exc:
            logger.warning("[worker] burst send failed (part %d/%d): %s",
                           i + 1, len(parts), exc)
            break
    return sent


def _humanize_gap(mins: float) -> str:
    if mins < 60:
        return f"about {int(mins)} minutes"
    hrs = mins / 60.0
    if hrs < 24:
        n = int(round(hrs))
        return f"about {n} hour{'s' if n != 1 else ''}"
    days = int(round(hrs / 24.0))
    return f"about {days} day{'s' if days != 1 else ''}"


async def _touch_message_at(user_id: str) -> None:
    """Fire-and-forget bump of user_machines.last_message_at."""
    from .router.db import touch_message_at
    try:
        await touch_message_at(user_id)
    except Exception as exc:
        logger.debug("[worker] touch_message_at failed: %s", exc)


def _register_channel_senders(session_id: str, bot_app, chat_id: int, loop,
                              photo_sent_flag: list,
                              captions_sent: list) -> None:
    """Wire send_photo / send_file → this user's Telegram bot, capturing
    side-state (did a photo go out, what caption shipped) so the caller
    can reconcile the saved Pg turn afterwards."""
    from .core.tools import set_file_sender, set_photo_sender

    def _file_sender(path: str, caption: str) -> None:
        with open(path, "rb") as f:
            data = f.read()
        coro = bot_app.bot.send_document(
            chat_id=chat_id, document=data,
            filename=os.path.basename(path),
            caption=(caption or "")[:1024] or None,
        )
        asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=60)
        photo_sent_flag.append(True)
        captions_sent.append(caption or "")

    def _photo_sender(path: str, caption: str) -> None:
        with open(path, "rb") as f:
            data = f.read()
        coro = bot_app.bot.send_photo(
            chat_id=chat_id, photo=data,
            caption=(caption or "")[:1024] or None,
        )
        asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=60)
        photo_sent_flag.append(True)
        captions_sent.append(caption or "")

    set_file_sender(session_id, _file_sender)
    set_photo_sender(session_id, _photo_sender)


async def _overwrite_last_assistant_turn(*, user_id: str, session_id: str,
                                         new_content: str) -> None:
    """Replace the most recent assistant turn's ``content`` for this session.

    Used after the agent emits a photo: the LLM saved its trailing
    narrative text to Pg (e.g. "Here's the king of the house, told you
    he'd be photogenic"), but TG only shipped the actual photo caption.
    We rewrite the Pg row so web Chronicles displays the caption that
    the user actually saw — keeping web and TG text in lockstep.

    Best-effort: any failure logs and returns without raising; the worst
    case is a one-message drift in the Chronicles view, not a broken
    chat.
    """
    if not new_content.strip():
        return
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return
    import httpx

    from .core.storage_pg import _hash_turn
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        # Step 1: find the latest assistant turn for this session so we
        # can target it by content_hash (PostgREST PATCH doesn't honour
        # order+limit on its own).
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{url}/rest/v1/turns",
                params={
                    "user_id":    f"eq.{user_id}",
                    "session_id": f"eq.{session_id}",
                    "role":       "eq.assistant",
                    "select":     "content_hash",
                    "order":      "ts.desc",
                    "limit":      "1",
                },
                headers=headers,
            )
            if not r.is_success:
                logger.warning("[worker] overwrite-turn lookup failed: %s",
                               r.status_code)
                return
            rows = r.json() or []
            if not rows:
                return
            old_hash = rows[0].get("content_hash")
            if not old_hash:
                return
            # Step 2: PATCH that row.  Recompute content_hash so future
            # saves of the same caption text dedupe cleanly.
            new_hash = _hash_turn(user_id, session_id, "assistant", new_content)
            r2 = await client.patch(
                f"{url}/rest/v1/turns",
                params={
                    "user_id":      f"eq.{user_id}",
                    "content_hash": f"eq.{old_hash}",
                },
                json={"content": new_content, "content_hash": new_hash},
                headers=headers,
            )
            if not r2.is_success:
                logger.warning("[worker] overwrite-turn PATCH failed: %s %s",
                               r2.status_code, r2.text[:200])
    except Exception as exc:
        logger.warning("[worker] overwrite-turn errored: %s", exc)


async def _emit_milestones(user_id: str) -> None:
    """Trip any newly-reached milestone rows in Pg.  Idempotent — the
    emitter dedupes on payload.key so repeated calls are cheap."""
    from .core.milestones import maybe_emit_after_turn
    try:
        await maybe_emit_after_turn(user_id)
    except Exception as exc:
        logger.debug("[worker] milestone emit failed: %s", exc)


async def _maybe_mark_onboarded(user_id: str, agent) -> None:
    """If the agent's memory has a bot_name, flip onboarded=true so the
    scheduler can begin firing proactive/selfie/planner.  Idempotent;
    db helper is a no-op once the column is already true."""
    try:
        has_bot_name = bool(
            (agent.memory.list_all() or {}).get("bot_name", "").strip()
        )
    except Exception:
        return
    if not has_bot_name:
        return
    from .router.db import mark_onboarded
    try:
        await mark_onboarded(user_id)
    except Exception as exc:
        logger.debug("[worker] mark_onboarded failed: %s", exc)


def _has_companion_in_pg(user_id: str) -> bool:
    """Sync check: does this user have a saved companion in Postgres?

    Used to gate the chat path — without it the worker refuses to engage
    and tells the user to finish the web wizard.  Synchronous httpx call
    is fine here; we already block on the agent's LLM call right after.
    """
    import httpx
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        # Single-tenant / dev environment without Supabase — fall back
        # to the legacy file check.
        from . import companion as comp
        return bool(comp.load_choices())
    try:
        r = httpx.get(
            f"{url}/rest/v1/user_companion",
            params={"user_id": f"eq.{user_id}", "select": "user_id"},
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
            },
            timeout=5,
        )
        return r.is_success and bool(r.json() or [])
    except Exception as exc:
        logger.warning("[worker] companion lookup failed: %s", exc)
        return False


def _hydrate_persona_from_pg(user_id: str) -> None:
    """On boot, materialize SOUL.md / PERSONA.md / PROFILE.md from the
    Pg-stored choices so the Agent's persona loader (which reads from
    /data/context/*.md) picks them up.  Silent no-op if the user hasn't
    completed the wizard yet."""
    from . import companion as comp

    # Fresh machine (destroyed / migrated / first boot)? Restore the user's
    # synthesized long-term memory from Tigris before the agent loads it.
    # No-op when local memory already exists.
    try:
        from .core import memory_backup
        memory_backup.restore(user_id)
    except Exception as exc:
        logger.debug("[worker] memory restore skipped: %s", exc)

    choices = comp.load_choices()
    if not choices:
        logger.info("[worker] no companion choices in Pg yet — chat blocked "
                    "until user completes web wizard")
        return
    # If the user materially re-customized (identity / place / language), wipe
    # the accumulated memory + transcript first so she doesn't behave like the
    # new persona while remembering the old one. Photos are kept. No-op on first
    # onboarding and on unchanged identity. Must run BEFORE apply_choices, which
    # regenerates the identity docs.
    try:
        from .core import recustomize
        recustomize.maybe_reset_on_identity_change(user_id, choices)
    except Exception as exc:
        logger.warning("[worker] identity-change reset skipped: %s", exc)
    try:
        comp.apply_choices(choices)
        logger.info("[worker] hydrated persona from Pg for user=%s", user_id[:8])
    except Exception as exc:
        logger.warning("[worker] persona hydrate failed: %s", exc)


# ── Entry point ──────────────────────────────────────────────────────


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = create_worker_app()
    uvicorn.run(
        app, host="0.0.0.0",
        port=int(os.environ.get("PORT", "7788")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
