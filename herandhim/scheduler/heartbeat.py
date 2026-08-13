"""
Heartbeat monitor for herandhim.

Periodically sends a minimal probe to the configured LLM provider to verify
that it is reachable and responding.  Results are:

  * Logged to context/logs/heartbeat.log (rotating, max 1 MB x 3 files)
  * Printed to stdout at DEBUG level
  * Optionally sent as a Telegram alert when the provider becomes unreachable
    (one alert per outage, not once per failed ping)

Configuration (herandhim.json or env vars)
-------------------------------------------
  heartbeat.intervalSec  / HEARTBEAT_INTERVAL_SEC   — seconds between probes (default: 60)
  heartbeat.alertChatId  / HEARTBEAT_ALERT_CHAT_ID  — Telegram chat_id to receive failure alerts
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import time
from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from ..channels.telegram_bot import TelegramBot
    from ..core.llm.base import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 60
def _log_dir() -> str:
    from .. import config as _cfg
    return os.path.join(str(_cfg.HERANDHIM_HOME), "context", "logs")


def _log_file() -> str:
    return os.path.join(_log_dir(), "heartbeat.log")

# Minimal probe message sent to the LLM
_PROBE_MESSAGES = [{"role": "user", "content": "ping"}]


class HeartbeatMonitor:
    """Async heartbeat that pings the LLM provider on a fixed interval."""

    def __init__(
        self,
        provider: "LLMProvider",
        interval_sec: int = DEFAULT_INTERVAL,
        telegram_bot: "TelegramBot | None" = None,
        alert_chat_id: int | None = None,
        log_path: str | None = None,
    ) -> None:
        self._provider = provider
        self._interval = interval_sec
        self._telegram_bot = telegram_bot
        self._alert_chat_id = alert_chat_id
        if log_path is None:
            try:
                log_path = _log_file()
            except Exception:
                log_path = os.path.join(os.path.expanduser("~/.herandhim"), "context", "logs", "heartbeat.log")
        self._log_path = log_path
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_ok: bool | None = None  # track state to avoid alert storms
        self._file_logger = _build_file_logger(log_path)

    # ── Public API ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the heartbeat loop as a background asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="heartbeat")
        logger.info("[Heartbeat] Monitor started (interval=%ds)", self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[Heartbeat] Monitor stopped.")

    # ── Internal loop ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            await self._probe()
            await asyncio.sleep(self._interval)

    async def _probe(self) -> None:
        start = time.monotonic()
        ok = False
        error_msg = ""
        try:
            # Run the blocking provider call in a thread-pool so we don't block the event loop
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._provider.chat(messages=_PROBE_MESSAGES, tools=[], tool_choice="none"),
            )
            _ = response.choices[0].message.content  # verify structure
            ok = True
        except Exception as exc:
            error_msg = str(exc)

        latency_ms = int((time.monotonic() - start) * 1000)
        self._log(ok, latency_ms, error_msg)
        await self._maybe_alert(ok, error_msg)

    def _log(self, ok: bool, latency_ms: int, error_msg: str) -> None:
        status = "OK" if ok else "FAIL"
        entry = f"[Heartbeat] {status} | latency={latency_ms}ms"
        if not ok:
            entry += f" | error={error_msg}"
        if ok:
            logger.debug(entry)
        else:
            logger.warning(entry)
        self._file_logger.info(entry)

    async def _maybe_alert(self, ok: bool, error_msg: str) -> None:
        if self._telegram_bot is None or self._alert_chat_id is None:
            self._last_ok = ok
            return

        if not ok and self._last_ok is not False:
            # Transition to failure → send alert
            msg = f"🚨 *Heartbeat FAILED*\n\nThe LLM provider is not responding.\n\nError: `{error_msg}`"
            try:
                await self._telegram_bot.send_message(self._alert_chat_id, msg)
            except Exception as exc:
                logger.error("[Heartbeat] Failed to send Telegram alert: %s", exc)
        elif ok and self._last_ok is False:
            # Recovered → send recovery notice
            msg = "✅ *Heartbeat RECOVERED*\n\nThe LLM provider is responding again."
            try:
                await self._telegram_bot.send_message(self._alert_chat_id, msg)
            except Exception as exc:
                logger.error("[Heartbeat] Failed to send Telegram recovery: %s", exc)

        self._last_ok = ok


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_file_logger(log_path: str) -> logging.Logger:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    file_logger = logging.getLogger("herandhim.heartbeat.file")
    file_logger.setLevel(logging.INFO)
    if not file_logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        file_logger.addHandler(handler)
    return file_logger


def create_heartbeat(
    provider: "LLMProvider",
    telegram_bot: "TelegramBot | None" = None,
) -> HeartbeatMonitor:
    """Create a HeartbeatMonitor from herandhim.json / env vars."""
    interval = config.get_int(
        "heartbeat", "intervalSec", env="HEARTBEAT_INTERVAL_SEC", default=DEFAULT_INTERVAL,
    )
    raw_chat_id = config.get_str(
        "heartbeat", "alertChatId", env="HEARTBEAT_ALERT_CHAT_ID",
    )
    alert_chat_id = int(raw_chat_id) if raw_chat_id else None
    return HeartbeatMonitor(
        provider=provider,
        interval_sec=interval,
        telegram_bot=telegram_bot,
        alert_chat_id=alert_chat_id,
    )


# Backward-compatible alias
create_heartbeat_from_env = create_heartbeat
