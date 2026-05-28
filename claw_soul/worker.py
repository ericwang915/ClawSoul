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
    "paid":       600,    # 10 min — proactive ticks land every 5 min
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
    return _tier() in ("paid", "enterprise")


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

    if kind == "proactive_tick":
        await _handle_proactive(state)
        return

    if kind == "planner_tick":
        await _handle_planner(state)
        return

    if kind == "selfie_tick":
        await _handle_selfie(state, slot=payload.get("slot"))
        return

    raise HTTPException(status_code=400, detail=f"unknown kind: {kind!r}")


async def _handle_telegram_update(update: dict, state: _WorkerState) -> None:
    update_id = update.get("update_id")
    if isinstance(update_id, int) and state.mark_update_seen(update_id):
        logger.info("[worker] dedup: skipping replayed update_id=%s", update_id)
        return

    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = ((msg.get("chat") or {}).get("id"))
    if not text or chat_id is None:
        return  # non-text message types not supported in this phase

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

    bot_app = await state.get_bot_app()
    if bot_app is None:
        logger.warning("[worker] no bot token configured; can't reply")
        return

    # Register photo/file senders for the agent's session so when the LLM
    # calls take_selfie → send_photo, the bytes actually flow through
    # this user's Telegram bot.  Without this the send_photo helper
    # returns "No active channel to send through" and the agent has to
    # apologise to the user.
    _register_channel_senders(agent._session_id, bot_app, chat_id, loop)  # noqa: SLF001

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
            return agent.chat(text)
        reply = await loop.run_in_executor(None, _chat_in_thread)
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except (asyncio.CancelledError, Exception):
            pass

    if not reply:
        return
    try:
        await bot_app.bot.send_message(chat_id=chat_id, text=reply[:4096])
    except Exception as exc:
        logger.warning("[worker] send_message failed: %s", exc)
        return

    # Outbound succeeded — touch last_message_at, mark onboarded once
    # bot_name lands in memory, and emit any milestones that just
    # tripped (first chat, day-streak, message-count, bonding level up).
    # All three are background tasks so they never block the reply.
    asyncio.create_task(_touch_message_at(state.user_id))
    asyncio.create_task(_maybe_mark_onboarded(state.user_id, agent))
    asyncio.create_task(_emit_milestones(state.user_id))


async def _handle_proactive(state: _WorkerState) -> None:
    """Proactive ticks land here; we reuse ProactiveMessenger by calling
    its generation function directly without the per-process scheduler."""
    from .scheduler.proactive import _build_prompt
    row = await _user_settings_lookup(state.user_id)
    chat_id = (row or {}).get("telegram_chat_id")
    if chat_id is None:
        return

    loop = asyncio.get_event_loop()
    agent = await state.get_agent()
    prompt = _build_prompt(datetime.now())
    pinned_uid = state.user_id
    def _chat_proactive():
        tenancy.set_current_user(pinned_uid)
        return agent.chat(prompt)
    text = await loop.run_in_executor(None, _chat_proactive)
    if not text:
        return
    bot_app = await state.get_bot_app()
    if bot_app:
        await bot_app.bot.send_message(chat_id=int(chat_id), text=text[:4096])


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
    def _gen_selfie():
        tenancy.set_current_user(pinned_uid)
        return take_selfie()
    try:
        result = await loop.run_in_executor(None, _gen_selfie)
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
                caption=result.caption()[:1024] or None,
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

    while True:
        await asyncio.sleep(30)
        idle = time.monotonic() - state.last_active
        if idle >= threshold:
            logger.info("[worker] idle for %.0fs ≥ %ds (tier=%s), exiting",
                        idle, threshold, _tier())
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


async def _touch_message_at(user_id: str) -> None:
    """Fire-and-forget bump of user_machines.last_message_at."""
    from .router.db import touch_message_at
    try:
        await touch_message_at(user_id)
    except Exception as exc:
        logger.debug("[worker] touch_message_at failed: %s", exc)


def _register_channel_senders(session_id: str, bot_app, chat_id: int, loop) -> None:
    """Wire up send_photo / send_file → this user's Telegram bot.

    The agent's tool layer calls ``send_photo(path)`` / ``send_file(path)``
    on a synchronous thread (inside ``agent.chat``), but PTB's
    ``bot.send_photo`` is async.  Bridge the two by scheduling the coroutine
    on the asyncio event loop and waiting for it from the worker thread.
    """
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

    def _photo_sender(path: str, caption: str) -> None:
        with open(path, "rb") as f:
            data = f.read()
        coro = bot_app.bot.send_photo(
            chat_id=chat_id, photo=data,
            caption=(caption or "")[:1024] or None,
        )
        asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=60)

    set_file_sender(session_id, _file_sender)
    set_photo_sender(session_id, _photo_sender)


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
    choices = comp.load_choices()
    if not choices:
        logger.info("[worker] no companion choices in Pg yet — chat blocked "
                    "until user completes web wizard")
        return
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
