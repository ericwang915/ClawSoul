"""
Minimal Telegram Bot API helpers for the router service.

The full PTB Application lives in the worker (it's what actually
processes messages); the router only needs three calls:

  - setWebhook       — point a user's bot at our router URL after they save the token
  - deleteWebhook    — undo if the token is cleared or the user downgrades to polling
  - sendMessage      — outbound fallback when the worker can't be woken (rare; nice-to-have)

Webhook secret
--------------
We protect the public /telegram/{token} endpoint with Telegram's
``secret_token`` mechanism: a per-bot HMAC of ROUTER_WEBHOOK_SALT is
included on every Telegram POST as the ``X-Telegram-Bot-Api-Secret-Token``
header.  Both sides derive it from (salt, bot_token), so no DB read is
needed to verify — the router computes the expected value from the path
token.  This stops anyone who guesses the URL from forging updates.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

import httpx

logger = logging.getLogger(__name__)


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def webhook_url_for(bot_token: str) -> str:
    """The public URL Telegram should POST updates to for *bot_token*."""
    base = os.environ.get("ROUTER_PUBLIC_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("ROUTER_PUBLIC_URL not set (e.g. https://clawsoul-router.fly.dev)")
    return f"{base}/telegram/{bot_token}"


def webhook_secret_for(bot_token: str) -> str:
    """Stateless per-bot webhook secret. Derived from ROUTER_WEBHOOK_SALT
    so router instances compute the same value without storing it.

    Format: lowercase hex (Telegram accepts ASCII alphanumerics + `_` `-`).
    Length 64 chars (sha256 hex).  If the salt isn't configured we return
    an empty string and skip verification — backwards-compatible with the
    pre-secret deployment.
    """
    salt = os.environ.get("ROUTER_WEBHOOK_SALT", "").strip()
    if not salt:
        return ""
    return hmac.new(salt.encode("utf-8"), bot_token.encode("utf-8"),
                    hashlib.sha256).hexdigest()


async def set_webhook(bot_token: str, *, drop_pending: bool = True) -> tuple[bool, str | None]:
    """Call Telegram setWebhook — point the user's bot at our router URL."""
    url = webhook_url_for(bot_token)
    body: dict = {
        "url": url,
        "drop_pending_updates": drop_pending,
        "allowed_updates": ["message", "edited_message", "callback_query"],
    }
    secret = webhook_secret_for(bot_token)
    if secret:
        body["secret_token"] = secret
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(_api(bot_token, "setWebhook"), json=body)
        data = r.json()
    except Exception as exc:
        return False, f"network/parse error: {exc}"

    if data.get("ok"):
        logger.info("[telegram] setWebhook ok for bot=%s… url=%s secret=%s",
                    bot_token[:8], url, "yes" if secret else "no")
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
