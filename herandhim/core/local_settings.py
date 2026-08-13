"""
Local single-user settings — the Telegram bot token, its chat id, and
integration API keys, persisted in ``herandhim.json``.

The hosted product kept these per-user in Postgres; the self-hosted build has
exactly one user, so they live in the local config file alongside everything
else. Thin read/write helpers used by the web dashboard's Telegram-connect
panel and the Arsenal (integrations) tab.
"""

from __future__ import annotations

import json
import re

from .. import config

# Telegram bot tokens look like ``123456789:AA<35 chars>``.
BOT_TOKEN_RE = re.compile(r"^\d{6,15}:[A-Za-z0-9_-]{30,}$")


def _write(mutate) -> None:
    """Load the config dict, apply ``mutate(dict)`` in place, persist, reload."""
    data = config.as_dict() or {}
    mutate(data)
    path = config.config_path() or (config.HERANDHIM_HOME / "herandhim.json")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    config.load(str(path), force=True)


# ── Telegram ──────────────────────────────────────────────────────────────

def get_telegram() -> dict:
    """Presence flag + chat id — never echoes the token itself."""
    return {
        "hasToken": bool(config.get_str("channels", "telegram", "token", default="")),
        "chatId": config.get("channels", "telegram", "chatId", default=None),
    }


_UNSET = object()


def set_telegram(token=_UNSET, chat_id=_UNSET) -> None:
    """Update the Telegram token and/or chat id in local config.

    Each field is only touched when passed: ``token=""`` clears the token,
    a non-empty ``token`` sets it, and omitting ``token`` leaves it as-is
    (so learning the chat id never wipes the token, and vice versa).
    """
    def _mut(d: dict) -> None:
        tg = d.setdefault("channels", {}).setdefault("telegram", {})
        if token is not _UNSET:
            if token:
                tg["token"] = token
            else:
                tg.pop("token", None)
        if chat_id is not _UNSET and chat_id is not None:
            tg["chatId"] = chat_id
    _write(_mut)


# ── Integrations (Arsenal API-key tools) ────────────────────────────────────

def get_integrations() -> dict:
    return config.get("integrations", default={}) or {}


def set_integration(name: str, value: dict | None) -> None:
    def _mut(d: dict) -> None:
        integ = d.setdefault("integrations", {})
        if value is None:
            integ.pop(name, None)
        else:
            integ[name] = value
    _write(_mut)
