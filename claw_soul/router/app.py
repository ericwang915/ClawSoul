"""
FastAPI app for the ClawSoul router/scheduler service.

Endpoints:

  GET  /health                       — liveness for Fly health check
  POST /telegram/{bot_token}         — Telegram webhook receiver, forwards
                                       to the user's worker /dispatch
  POST /admin/users/{uid}/provision  — spawn worker machine
  POST /admin/users/{uid}/destroy    — terminate it
  POST /admin/users/{uid}/wake       — manual wake (debugging)
  POST /admin/users/{uid}/webhook    — re-(un)set Telegram webhook
  GET  /admin/users                  — list user_machines rows

Auth: most endpoints require ``X-Admin-Key`` header matching env
``ROUTER_ADMIN_KEY``.  ``/health`` and ``/telegram/{token}`` are public —
they're authenticated by Fly's health checker / by the token in the path.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import fly_client
from . import db, dispatch, scheduler, telegram_api

logger = logging.getLogger(__name__)


def _check_admin(x_admin_key: str | None) -> None:
    expected = os.environ.get("ROUTER_ADMIN_KEY", "").strip()
    if not expected:
        # Operator hasn't set the key — refuse all admin ops rather than
        # accidentally exposing them.
        raise HTTPException(status_code=503,
                            detail="ROUTER_ADMIN_KEY not configured")
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="bad admin key")


def create_router_app() -> FastAPI:
    app = FastAPI(title="ClawSoul Router", docs_url=None, redoc_url=None)
    sched = scheduler.get_scheduler()

    # ── Lifecycle ────────────────────────────────────────────────────

    @app.on_event("startup")
    async def _startup() -> None:
        try:
            await sched.start()
        except Exception as exc:
            logger.exception("[router] scheduler start failed: %s", exc)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        try:
            await sched.stop()
        except Exception:
            pass

    # ── Liveness ─────────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "service": "clawsoul-router",
            "version": _version(),
            "scheduled_users": len(sched._known_users),  # noqa: SLF001
        })

    # ── Telegram webhook ─────────────────────────────────────────────

    @app.post("/telegram/{bot_token}")
    async def telegram_webhook(
        bot_token: str,
        request: Request,
        background_tasks: BackgroundTasks,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> JSONResponse:
        """Receive a Telegram update → fan out to the worker.

        Returns 200 immediately and dispatches in the background so cold-
        starting a worker (5-10 s) doesn't trigger Telegram retries. The
        worker dedupes by update_id so a retry that does happen is safe.

        Auth: the bot_token in the path identifies the user; the
        ``X-Telegram-Bot-Api-Secret-Token`` header proves the request is
        actually from Telegram (HMAC of ROUTER_WEBHOOK_SALT + bot_token).
        Backwards-compatible: if the env salt isn't set, we skip the
        header check and rely on path-token obscurity only.
        """
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON")

        expected_secret = telegram_api.webhook_secret_for(bot_token)
        if expected_secret:
            got = x_telegram_bot_api_secret_token or ""
            if not hmac.compare_digest(got, expected_secret):
                logger.warning("[router] webhook secret mismatch bot=%s…",
                               bot_token[:8])
                return JSONResponse({"ignored": True}, status_code=200)

        user_id = await db.find_user_id_by_bot_token(bot_token)
        if not user_id:
            return JSONResponse({"ignored": True}, status_code=200)

        background_tasks.add_task(
            dispatch.dispatch, user_id, "telegram_update", {"update": payload},
        )
        return JSONResponse({"queued": True})

    # ── Admin: per-user machine lifecycle ────────────────────────────

    @app.post("/admin/users/{user_id}/provision")
    async def provision(
        user_id: str,
        request: Request,
        x_admin_key: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_admin(x_admin_key)
        body = await _read_json_or_default(request)
        tier = (body or {}).get("tier") or "free"
        region = (body or {}).get("region") or fly_client._default_region()  # noqa: SLF001

        # Need the bot token to set the webhook atomically.  Without one,
        # the user can't receive any messages — refuse to provision
        # rather than leaving them in a half-set-up state.
        settings = await db.get_user_setting_row(user_id)
        if not settings or not settings.get("telegram_bot_token"):
            raise HTTPException(400, "user has no telegram_bot_token; "
                                "save token via dashboard before provisioning")
        bot_token = settings["telegram_bot_token"]

        # 1. Machine (idempotent on user_machines.user_id PK).
        existing = await db.get_user_machine(user_id)
        if existing is None:
            try:
                spec = fly_client.MachineSpec(
                    user_id=user_id, region=region, tier=tier,
                )
                machine = fly_client.create_user_machine(spec)
            except fly_client.FlyConfigError as exc:
                raise HTTPException(503, f"fly not configured: {exc}")
            except fly_client.FlyAPIError as exc:
                raise HTTPException(502, f"fly api error: {exc}")
            await db.upsert_user_machine(
                user_id,
                machine_id=machine["id"],
                region=machine.get("region", region),
                state="starting",
                tier=tier,
                image_ref=spec.image or fly_client._worker_image(),  # noqa: SLF001
            )
            machine_id = machine["id"]
        else:
            machine_id = existing.machine_id

        # 2. Webhook — AFTER machine exists so any inbound update finds a
        # target.  Idempotent: calling setWebhook with the same URL is a no-op.
        ok, url_or_err = await telegram_api.set_webhook(bot_token)
        if ok:
            await db.upsert_user_machine(user_id, webhook_url=url_or_err)
        else:
            logger.warning("[router] provision setWebhook failed: %s", url_or_err)

        # 2b. If the wizard was completed BEFORE this provision (user
        # finished web onboarding then we created the row), the wizard's
        # PATCH onboarded=true was a no-op on a non-existent row.  Catch
        # that retroactively here.
        if await db.user_companion_exists(user_id):
            await db.mark_onboarded(user_id)

        # 3. Pull into scheduler (claim PK already dedupes ticks).
        if tier != "free":
            await scheduler.kick_reconcile()
        return JSONResponse({
            "ok": True, "reused": existing is not None,
            "machine_id": machine_id,
            "state": (existing.state if existing else "starting"),
            "webhook_ok": ok,
        })

    @app.post("/admin/users/{user_id}/destroy")
    async def destroy(
        user_id: str,
        x_admin_key: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_admin(x_admin_key)
        row = await db.get_user_machine(user_id)
        if row is None:
            return JSONResponse({"ok": True, "note": "no machine"}, status_code=200)
        try:
            fly_client.destroy_machine(row.machine_id, force=True)
        except fly_client.FlyAPIError as exc:
            logger.warning("[router] destroy fly err: %s", exc)
        await db.delete_user_machine(user_id)
        await scheduler.kick_reconcile()
        return JSONResponse({"ok": True, "machine_id": row.machine_id})

    @app.post("/admin/users/{user_id}/wake")
    async def wake(
        user_id: str,
        x_admin_key: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_admin(x_admin_key)
        row = await db.get_user_machine(user_id)
        if row is None:
            raise HTTPException(404, "user_machines row not found")
        try:
            await dispatch.wake_if_needed(row)
        except dispatch.DispatchError as exc:
            raise HTTPException(502, str(exc))
        return JSONResponse({"ok": True, "machine_id": row.machine_id})

    @app.post("/admin/users/{user_id}/webhook")
    async def webhook_set(
        user_id: str,
        request: Request,
        x_admin_key: str | None = Header(default=None),
    ) -> JSONResponse:
        """(Re)set Telegram webhook for this user, using whatever token is
        currently on their user_settings row.  Pass ``{"action":"delete"}``
        in the body to clear the webhook instead."""
        _check_admin(x_admin_key)
        body = await _read_json_or_default(request)
        action = (body or {}).get("action") or "set"

        s = await db.get_user_setting_row(user_id)
        if not s or not s.get("telegram_bot_token"):
            raise HTTPException(404, "user has no telegram_bot_token")
        token = s["telegram_bot_token"]

        if action == "delete":
            ok = await telegram_api.delete_webhook(token)
            await db.upsert_user_machine(user_id, webhook_url=None)
            return JSONResponse({"ok": ok, "action": "delete"})

        ok, url_or_err = await telegram_api.set_webhook(token)
        if not ok:
            raise HTTPException(502, f"setWebhook failed: {url_or_err}")
        await db.upsert_user_machine(user_id, webhook_url=url_or_err)
        return JSONResponse({"ok": True, "webhook_url": url_or_err})

    @app.get("/admin/users")
    async def list_users(
        x_admin_key: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_admin(x_admin_key)
        rows = await db.list_user_machines()
        return JSONResponse({
            "users": [
                {
                    "user_id": r.user_id, "machine_id": r.machine_id,
                    "region": r.region, "state": r.state, "tier": r.tier,
                    "webhook_url": r.webhook_url,
                }
                for r in rows
            ],
        })

    return app


async def _read_json_or_default(request: Request) -> dict | None:
    if not await request.body():
        return None
    try:
        return await request.json()
    except Exception:
        return None


def _version() -> str:
    return os.environ.get("FLY_MACHINE_VERSION") or "dev"
