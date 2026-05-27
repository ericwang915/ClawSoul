"""
Minimal Telegram Bot API helpers for the router service.

The full PTB Application lives in the worker (it's what actually
processes messages); the router only needs three calls:

  - setWebhook       — point a user's bot at our router URL after they save the token
  - deleteWebhook    — undo if the token is cleared or the user downgrades to polling
  - sendMessage      — outbound fallback when the worker can't be woken (rare; nice-to-have)
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def webhook_url_for(bot_token: str) -> str:
    """The public URL Telegram should POST updates to for *bot_token*.

    The path includes the token itself (with a leading slash) so the router
    can authenticate the request just by URL routing — Telegram only knows
    this URL because we set it, and we set it scoped to the matching user.
    """
    base = os.environ.get("ROUTER_PUBLIC_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("ROUTER_PUBLIC_URL not set (e.g. https://clawsoul-router.fly.dev)")
    return f"{base}/telegram/{bot_token}"


async def set_webhook(bot_token: str, *, drop_pending: bool = True) -> tuple[bool, str | None]:
    """Call Telegram setWebhook — point the user's bot at our router URL."""
    url = webhook_url_for(bot_token)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(_api(bot_token, "setWebhook"), json={
                "url": url,
                "drop_pending_updates": drop_pending,
                "allowed_updates": ["message", "edited_message", "callback_query"],
            })
        data = r.json()
    except Exception as exc:
        return False, f"network/parse error: {exc}"

    if data.get("ok"):
        logger.info("[telegram] setWebhook ok for bot=%s… url=%s",
                    bot_token[:8], url)
        return True, url
    return False, data.get("description") or "unknown"


async def delete_webhook(bot_token: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(_api(bot_token, "deleteWebhook"),
                             json={"drop_pending_updates": False})
        return bool(r.json().get("ok"))
    except Exception:
        return False


async def send_message(bot_token: str, chat_id: int, text: str) -> bool:
    """Outbound message fallback (e.g. router-side maintenance notice)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(_api(bot_token, "sendMessage"), json={
                "chat_id": chat_id, "text": text[:4096],
            })
        return bool(r.json().get("ok"))
    except Exception:
        return False
