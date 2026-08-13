"""
Telegram channel for herandhim — the companion's home on your own bot.

Long-polling bot: it connects out to Telegram (no public URL / webhook
needed), so it runs anywhere, including behind a home NAT. Delivery-side
human-feel (voice transcription, emoji reactions, message bursts, absence
awareness) lives in ``core/humanize.py`` and is applied here for companion
sessions.

Telegram is purely a *channel* — it handles sending and receiving messages.
Session lifecycle (which Agent handles which chat) is delegated to the
SessionManager, which is shared across all channels and the cron scheduler.

Session IDs used by this channel: "telegram:{chat_id}"

Commands
--------
  /start          — greeting + usage hint
  /reset          — discard and recreate the current session
  /status         — show session info (provider, skills, memory, tokens, compactions)
  /compact [hint] — compact conversation history
  <text>          — forwarded to Agent.chat(), reply sent back
  <photo>         — image sent to LLM with optional caption

Access control
--------------
Set TELEGRAM_ALLOWED_USERS to a comma-separated list of integer Telegram user
IDs to restrict access.  Leave empty (or unset) to allow all users.

Group behaviour
---------------
Set ``channels.telegram.requireMention`` to ``true`` in herandhim.json to
require @bot mention in group chats.  DMs always respond.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import queue as _queue
import re
import time
from typing import TYPE_CHECKING

from telegram import BotCommand, ReactionTypeEmoji, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .. import config
from ..core.humanize import humanize_gap, maybe_react, reply_delay, send_burst

if TYPE_CHECKING:
    from ..session_manager import SessionManager

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Telegram channel — pure I/O layer.

    Receives messages from Telegram and routes them to the appropriate Agent
    via the shared SessionManager.  Does not own or manage Agent instances.
    """

    def __init__(
        self,
        session_manager: "SessionManager",
        token: str,
        allowed_users: list[int] | None = None,
        require_mention: bool = False,
    ) -> None:
        self._sm = session_manager
        self._token = token
        self._allowed_users: set[int] = set(allowed_users) if allowed_users else set()
        self._require_mention = require_mention
        self._app: Application | None = None
        self._bot_username: str | None = None
        # Once the chat_id is saved to local settings we don't try again,
        # even across reconnects within this process.
        self._chat_id_saved: bool = False
        # Invoked the first time we capture the chat_id, so the proactive
        # scheduler can register its job without a daemon restart.
        self._on_chat_id_learned = None
        # Per-chat timestamp of the previous inbound message — feeds the
        # companion's absence awareness (agent._gap_note).
        self._last_seen: dict[int, float] = {}

    # ── Session ID convention ─────────────────────────────────────────────────

    def _session_id(self, chat_id: int) -> str:
        """Session id for this chat.

        Partitioned by chat_id so one bot can serve several Telegram chats
        independently — each keeps its own history.
        """
        return f"telegram:{chat_id}"

    # ── Push message (called by cron / heartbeat) ─────────────────────────────

    async def send_message(self, chat_id: int, text: str) -> None:
        """Send a message to a specific chat (used by cron/heartbeat)."""
        if self._app is None:
            logger.warning("[Telegram] send_message called before bot is running")
            return
        await self._app.bot.send_message(chat_id=chat_id, text=text)

    async def send_photo(self, chat_id: int, path: str, caption: str = "") -> None:
        """Send a photo with inline preview (used by selfie scheduler)."""
        if self._app is None:
            logger.warning("[Telegram] send_photo called before bot is running")
            return
        with open(path, "rb") as f:
            await self._app.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=caption[:1024] if caption else None,
            )

    # ── Access control ────────────────────────────────────────────────────────

    def _is_allowed(self, user_id: int) -> bool:
        if not self._allowed_users:
            return True
        return user_id in self._allowed_users

    async def _remember_their_photo(self, agent, image_part: dict) -> None:
        """Describe a photo the user sent in one line and file it in long-term
        memory, keyed by date, so she can reference it days later.

        One cheap vision call, off the reply path. Silent on any failure —
        losing a memory is never worth delaying or breaking a reply.
        """
        try:
            from datetime import datetime as _dt
            provider = getattr(agent, "provider", None)
            if provider is None or not getattr(provider, "supports_images", False):
                return
            loop = asyncio.get_event_loop()

            def _describe() -> str:
                r = provider.chat(
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text":
                         "Describe this photo in ONE short line (max 15 words) — "
                         "what/where/mood. Output only the line."},
                        image_part,
                    ]}],
                    tools=[], temperature=0.2, max_tokens=60,
                )
                return (r.choices[0].message.content or "").strip()

            desc = await loop.run_in_executor(None, _describe)
            if not desc:
                return
            stamp = _dt.now().strftime("%Y-%m-%d_%H%M%S")
            agent.memory.remember(
                f"Photo they sent me on {stamp[:10]}: {desc}",
                key=f"their_photo_{stamp}",
            )
            logger.info("[Telegram] remembered their photo: %s", desc[:60])
        except Exception as exc:
            logger.debug("[Telegram] photo memory skipped: %s", exc)

    async def _maybe_save_chat_id(self, chat_id: int) -> None:
        """Learn and persist the user's Telegram chat_id to the local config
        the first time they message the bot — it's what the proactive
        scheduler uses to send unprompted messages. Idempotent per process."""
        if self._chat_id_saved:
            return
        try:
            from ..config import get as _cfg_get
            if _cfg_get("channels", "telegram", "chatId", default=None):
                self._chat_id_saved = True
                return
            from ..core import local_settings
            local_settings.set_telegram(chat_id=chat_id)  # token untouched
            self._chat_id_saved = True
            logger.info("[Telegram] learned chat_id=%s", chat_id)
            cb = self._on_chat_id_learned
            if cb is not None:
                try:
                    cb(chat_id)
                except Exception:
                    logger.warning("[Telegram] chat_id_learned callback failed",
                                   exc_info=True)
        except Exception as exc:
            logger.warning("[Telegram] chat_id save errored: %s", exc)

    async def _check_access(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        user = update.effective_user
        if user is None or not self._is_allowed(user.id):
            logger.warning("[Telegram] Rejected user_id=%s", user.id if user else "unknown")
            await update.message.reply_text("Sorry, you are not authorised to use this bot.")
            return False
        return True

    def _is_group(self, update: Update) -> bool:
        """Return True if the message is from a group/supergroup."""
        return update.effective_chat.type in ("group", "supergroup")

    def _is_mentioned(self, update: Update) -> bool:
        """Check if the bot is @mentioned in the message text."""
        text = update.message.text or update.message.caption or ""
        if self._bot_username and f"@{self._bot_username}" in text:
            return True
        entities = update.message.entities or update.message.caption_entities or []
        for ent in entities:
            if ent.type == "mention" and self._bot_username:
                mention = text[ent.offset:ent.offset + ent.length]
                if mention.lower() == f"@{self._bot_username.lower()}":
                    return True
        return False

    def _strip_mention(self, text: str) -> str:
        """Remove the @bot mention from message text."""
        if self._bot_username:
            text = text.replace(f"@{self._bot_username}", "").strip()
        return text

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_access(update, context):
            return
        sid = self._session_id(update.effective_chat.id)
        self._sm.get_or_create(sid)
        await update.message.reply_text(
            "\U0001f44b Hi! I'm your HerAndHim agent.\n\n"
            "Just send me a message and I'll do my best to help.\n"
            "You can also send photos and I'll analyze them.\n\n"
            "Commands:\n"
            "  /start          \u2014 show this message\n"
            "  /reset          \u2014 start a fresh session\n"
            "  /status         \u2014 show session info\n"
            "  /compact [hint] \u2014 compact conversation history\n"
            "  /clear_files    \u2014 delete all downloaded files"
        )

    async def _cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Wipe the rolling chat transcript and start a fresh session.

        Long-term memory (MEMORY.md), persona, soul, skills, and learned
        facts are preserved — only this conversation's history is reset.
        Also aliased as ``/clear``.
        """
        if not await self._check_access(update, context):
            return
        sid = self._session_id(update.effective_chat.id)
        self._sm.reset(sid)
        await update.message.reply_text(
            "🧹 Chat history cleared — fresh session.\n"
            "Long-term memory, persona, and what I know about you are kept.\n\n"
            "🧹 已清空对话记录，重新开始。\n"
            "你的资料、记忆和我们的关系还在。"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_access(update, context):
            return
        sid = self._session_id(update.effective_chat.id)
        agent = self._sm.get_or_create(sid)
        from ..core.compaction import estimate_tokens
        await update.message.reply_text(
            f"\U0001f4ca Session Status\n"
            f"  Session ID   : {sid}\n"
            f"  Provider     : {type(agent.provider).__name__}\n"
            f"  Skills       : {len(agent.loaded_skill_names)} loaded\n"
            f"  Memories     : {len(agent.memory.list_all())} entries\n"
            f"  History      : {len(agent.messages)} messages\n"
            f"  Est. tokens  : ~{estimate_tokens(agent.messages):,}\n"
            f"  Compactions  : {agent.compaction_count}\n"
            f"  Total sessions: {len(self._sm)}"
        )

    async def _cmd_compact(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_access(update, context):
            return
        sid = self._session_id(update.effective_chat.id)
        agent = self._sm.get_or_create(sid)
        hint: str | None = " ".join(context.args).strip() or None if context.args else None
        await update.message.reply_text("\u23f3 Compacting conversation history...")
        try:
            result = agent.compact(instruction=hint)
        except Exception as exc:
            result = f"Compaction failed: {exc}"
        for chunk in _split_message(result):
            await update.message.reply_text(chunk)

    async def _cmd_clear_files(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_access(update, context):
            return
        from .. import config as _cfg
        count = _cfg.clear_files()
        await update.message.reply_text(f"Cleared {count} file(s) from the downloads folder.")

    # ── Message handler (text + photos) ───────────────────────────────────────

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_access(update, context):
            return

        if self._is_group(update) and self._require_mention:
            if not self._is_mentioned(update):
                return

        # Capture the Telegram chat_id once so the
        # proactive scheduler knows where to message them. Best-effort —
        # any failure just means we'll retry on the next inbound message.
        await self._maybe_save_chat_id(update.effective_chat.id)

        user_text = (update.message.text or update.message.caption or "").strip()
        user_text = self._strip_mention(user_text)

        has_photo = bool(update.message.photo)
        has_voice = bool(update.message.voice or update.message.audio)

        if has_voice:
            transcript = await self._transcribe_voice(update)
            if transcript is None:
                return
            user_text = transcript

        if not user_text and not has_photo:
            return

        sid = self._session_id(update.effective_chat.id)
        agent = self._sm.get_or_create(sid)

        # Companion mode (a soul/persona is configured) delivers like a real
        # person texting: selective emoji reactions, absence awareness, a
        # human pause, and 1–3 message bubbles — the same behaviour as the
        # hosted worker, via the shared core.humanize module. Bare-assistant
        # sessions keep the streaming/edit-in-place flow below.
        is_companion = bool(getattr(agent, "soul_instruction", "")
                            or getattr(agent, "persona_instruction", ""))

        if is_companion:
            # Absence awareness: how long since THEIR previous message.
            now_ts = time.time()
            prev_ts = self._last_seen.get(update.effective_chat.id)
            self._last_seen[update.effective_chat.id] = now_ts
            gap_note = ""
            if prev_ts:
                mins = (now_ts - prev_ts) / 60.0
                if mins >= 20:
                    gap_note = humanize_gap(mins)
            agent._gap_note = gap_note
            # How many of HER messages went unanswered before this reply — lets
            # her be a little sulky about being ignored, with a real basis.
            # Read before clearing, since this message ends the streak.
            from ..core import proactive_state as _pstate
            agent._ignored_count = _pstate.unanswered(sid)
            _pstate.clear(sid)
            # Selective reaction — a photo almost always, big feelings
            # sometimes, logistics never. Fired before the reply, like a
            # human seeing the message first.
            asyncio.create_task(maybe_react(
                context.bot, update.effective_chat.id,
                update.message.message_id, has_photo, user_text))
        elif has_photo and not user_text:
            # Assistant mode: photo without caption → queue, don't generate.
            # The next text pops the queue and we answer once with all parts.
            try:
                parts = await self._build_image_input(update, "")
            except Exception:
                parts = []
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    agent.queue_attachment(p)
            try:
                await update.message.set_reaction(
                    [ReactionTypeEmoji("\U0001f4ce")],  # 📎
                )
            except Exception:
                pass
            return
        else:
            try:
                await update.message.set_reaction([ReactionTypeEmoji("\U0001f440")])
            except Exception:
                pass

        # Build the multimodal turn: queued images (from earlier photo-only
        # turns) + this turn's new image (if any) + the caption / text.
        queued = agent.consume_attachments()
        new_parts: list[dict] = []
        if has_photo:
            built = await self._build_image_input(update, "")
            new_parts = [p for p in built if isinstance(p, dict) and p.get("type") == "image_url"]

        all_attachments = queued + new_parts

        # Remember the photos THEY send, so she can bring one up later
        # ("that beach pic you sent last week") instead of seeing it once and
        # forgetting it. Background + best-effort: never blocks the reply.
        if is_companion and new_parts:
            asyncio.create_task(self._remember_their_photo(agent, new_parts[0]))
        if all_attachments:
            photo_placeholder = ("（我发了一张照片给你，看看然后自然地回应～）"
                                 if is_companion else "What's in this image?")
            chat_input = [
                {"type": "text", "text": user_text or photo_placeholder},
                *all_attachments,
            ]
        else:
            chat_input = user_text or ""

        typing_task = asyncio.create_task(
            self._keep_typing(update.message.chat_id)
        )
        try:
            async with self._sm.acquire(sid):
                loop = asyncio.get_event_loop()
                chat_id = update.effective_chat.id
                self._register_file_sender(loop, chat_id)
                if is_companion:
                    # Humanized delivery: full reply, then a length-scaled
                    # pause + 1-3 bubbles. (No live edit-in-place — watching
                    # a message rewrite itself reads as a bot, not a partner.)
                    reply = await loop.run_in_executor(None, agent.chat, chat_input)
                    typing_task.cancel()
                    if reply:
                        try:
                            await asyncio.sleep(reply_delay(reply))
                        except Exception:
                            pass
                        await send_burst(context.bot, chat_id, _clean_response(reply))
                else:
                    token_queue: _queue.Queue[str] = _queue.Queue()
                    future = loop.run_in_executor(
                        None, agent.chat_stream, chat_input, token_queue.put,
                    )
                    await self._flush_stream(update, token_queue, future)
        except Exception as exc:
            logger.exception("[Telegram] Agent error")
            await update.message.reply_text(f"Sorry, something went wrong: {exc}")
        finally:
            typing_task.cancel()

        # Assistant mode clears its 👀 "reading" reaction when done; a
        # companion's reaction (❤️ on your photo) is a real response — keep it.
        if not is_companion:
            try:
                await update.message.set_reaction([])
            except Exception:
                pass

    _AGENT_TIMEOUT = 600

    async def _flush_stream(
        self,
        update: Update,
        token_queue: "_queue.Queue[str]",
        future: "asyncio.Future[str]",
    ) -> None:
        """Collect streamed tokens and deliver as 2-3 large messages.

        Strategy: accumulate all tokens silently. Tool-call markers are
        stripped but do NOT trigger new messages.  Content is edit-in-place
        updated into a single live message; only when a message hits the
        Telegram 4096 char limit is a new message started.

        No heartbeat / "still working" messages are sent.
        """
        buf: list[str] = []
        live_msg = None
        live_text = ""
        sent_any = False
        THROTTLE = 2.0
        last_edit = time.monotonic()
        start_time = time.monotonic()
        _MARKER = re.compile(r'`\[calling:\s*([^\]]+)\]`')

        while not future.done():
            if (time.monotonic() - start_time) > self._AGENT_TIMEOUT:
                logger.warning(
                    "[Telegram] Agent timeout after %ds", self._AGENT_TIMEOUT,
                )
                break

            drained = False
            while True:
                try:
                    buf.append(token_queue.get_nowait())
                    drained = True
                except _queue.Empty:
                    break

            if not drained:
                await asyncio.sleep(0.4)
                continue

            raw = _MARKER.sub("", "".join(buf))
            text = _clean_response(raw)
            now = time.monotonic()

            # Only show up to the last paragraph break while streaming;
            # the trailing incomplete line is held back to avoid flashing
            # progress narration that will be stripped later.
            last_break = text.rfind("\n\n")
            display = text[:last_break].rstrip() if last_break > 0 else ""

            if display and display != live_text and (now - last_edit) >= THROTTLE:
                try:
                    if live_msg is None:
                        live_msg = await update.message.reply_text(
                            display[:4096],
                        )
                        live_text = display[:4096]
                    elif len(display) <= 4096:
                        await live_msg.edit_text(display)
                        live_text = display
                    else:
                        await live_msg.edit_text(display[:4096])
                        live_msg = None
                        live_text = ""
                        buf = [display[4096:] + text[last_break:]]
                    sent_any = True
                except Exception:
                    pass
                last_edit = now

            await asyncio.sleep(0.4)

        # ── Final drain ───────────────────────────────────────────────
        response = future.result() if future.done() else "(timed out)"
        while True:
            try:
                buf.append(token_queue.get_nowait())
            except _queue.Empty:
                break

        raw = _MARKER.sub("", "".join(buf))
        remaining = _clean_response(raw.strip())
        if remaining and remaining != live_text:
            try:
                if live_msg and len(remaining) <= 4096:
                    await live_msg.edit_text(remaining)
                elif live_msg:
                    await live_msg.edit_text(remaining[:4096])
                    for chunk in _split_message(remaining[4096:]):
                        await update.message.reply_text(chunk)
                else:
                    for chunk in _split_message(remaining):
                        await update.message.reply_text(chunk)
                sent_any = True
            except Exception:
                pass

        if not sent_any:
            text = _clean_response(response or "(no response)")
            for chunk in _split_message(text):
                await update.message.reply_text(chunk)

    def _register_file_sender(self, loop: asyncio.AbstractEventLoop, chat_id: int) -> None:
        """Register sync callbacks so the Agent can send files / photos via Telegram."""
        from ..core.tools import set_file_sender, set_photo_sender

        bot_app = self._app

        def _file_sender(path: str, caption: str = "") -> None:
            async def _do_send():
                try:
                    with open(path, "rb") as f:
                        await bot_app.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=caption[:1024] if caption else None,
                        )
                except Exception as exc:
                    logger.warning("[Telegram] send_file failed: %s", exc)

            future = asyncio.run_coroutine_threadsafe(_do_send(), loop)
            future.result(timeout=60)

        def _photo_sender(path: str, caption: str = "") -> None:
            async def _do_send():
                try:
                    with open(path, "rb") as f:
                        await bot_app.bot.send_photo(
                            chat_id=chat_id,
                            photo=f,
                            caption=caption[:1024] if caption else None,
                        )
                except Exception as exc:
                    logger.warning("[Telegram] send_photo failed: %s", exc)

            future = asyncio.run_coroutine_threadsafe(_do_send(), loop)
            future.result(timeout=60)

        set_file_sender(self._session_id(chat_id), _file_sender)
        set_photo_sender(self._session_id(chat_id), _photo_sender)

    async def _build_image_input(self, update: Update, caption: str) -> list:
        """Download photo and build a multimodal content array."""
        photo = update.message.photo[-1]  # highest resolution
        file = await photo.get_file()
        data = await file.download_as_bytearray()
        b64 = base64.b64encode(bytes(data)).decode()

        # Determine MIME type from the photo's file path extension
        mime_type = "image/jpeg"
        mime_map = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
        ext = os.path.splitext(file.file_path or "")[1].lower()
        if ext in mime_map:
            mime_type = mime_map[ext]

        return [
            {"type": "text", "text": caption},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{b64}",
                },
            },
        ]

    async def _transcribe_voice(self, update: Update) -> str | None:
        """Download a voice/audio message and transcribe via Deepgram.

        Returns the transcript text, or sends a hint to the user and
        returns ``None`` if Deepgram is not configured.
        """
        from ..core.stt import no_key_message, transcribe_bytes_async

        voice = update.message.voice or update.message.audio
        tg_file = await voice.get_file()
        audio_bytes = bytes(await tg_file.download_as_bytearray())
        mime = voice.mime_type or "audio/ogg"

        try:
            transcript = await transcribe_bytes_async(audio_bytes, mime)
        except Exception as exc:
            logger.warning("[Telegram] Deepgram transcription failed: %s", exc)
            await update.message.reply_text(f"Voice transcription failed: {exc}")
            return None

        if transcript is None:
            await update.message.reply_text(no_key_message())
            return None

        if not transcript.strip():
            await update.message.reply_text("Could not recognise any speech in the audio.")
            return None

        logger.info("[Telegram] Voice transcribed: %s", transcript[:80])
        return transcript

    async def _keep_typing(self, chat_id: int) -> None:
        """Re-send the 'typing' chat action every 4 s until cancelled."""
        try:
            while True:
                await self._app.bot.send_chat_action(chat_id=chat_id, action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("[Telegram] _keep_typing stopped unexpectedly", exc_info=True)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    _BOT_COMMANDS = [
        BotCommand("start",       "Show welcome message"),
        BotCommand("clear",       "🧹 Clear chat history (keep memory & persona)"),
        BotCommand("reset",       "Alias for /clear — same thing"),
        BotCommand("status",      "Show session info"),
        BotCommand("compact",     "Compact conversation history"),
        BotCommand("clear_files", "Delete all downloaded files"),
    ]


    def build_application(self) -> Application:
        app = Application.builder().token(self._token).build()
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("reset", self._cmd_reset))
        app.add_handler(CommandHandler("clear", self._cmd_reset))  # alias
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("compact", self._cmd_compact))
        app.add_handler(CommandHandler("clear_files", self._cmd_clear_files))
        app.add_handler(MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO)
            & ~filters.COMMAND,
            self._handle_message,
        ))
        self._app = app
        return app

    async def _register_commands(self) -> None:
        """Register slash-commands with Telegram so they appear in the menu."""
        try:
            await self._app.bot.set_my_commands(self._BOT_COMMANDS)
            me = await self._app.bot.get_me()
            self._bot_username = me.username
            logger.info(
                "[Telegram] Registered %d bot commands, username=@%s",
                len(self._BOT_COMMANDS), self._bot_username,
            )
        except Exception:
            logger.warning("[Telegram] Failed to register bot commands", exc_info=True)

    def run_polling(self) -> None:
        """Blocking call — starts the bot using long polling (for standalone use)."""
        app = self.build_application()
        logger.info("[Telegram] Starting bot (polling mode)...")
        app.post_init = lambda _app: self._register_commands()
        app.run_polling(drop_pending_updates=True)

    async def start_async(self) -> None:
        """Non-blocking start — for use inside an existing asyncio event loop."""
        app = self.build_application()
        logger.info("[Telegram] Initialising bot (async mode)...")
        await app.initialize()
        await app.start()
        await self._register_commands()
        await app.updater.start_polling(drop_pending_updates=True)

    async def stop_async(self) -> None:
        if self._app is None:
            return
        logger.info("[Telegram] Stopping bot...")
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()


# ── Utility ───────────────────────────────────────────────────────────────────

_LEAKED_TOOL_RE = re.compile(
    r'<\s*\|?\s*(?:DSML|antml)\s*\|\s*function_calls[^>]*>'
    r'[\s\S]*?'
    r'<\s*/\s*\|?\s*(?:DSML|antml)\s*\|\s*function_calls\s*>',
    re.IGNORECASE,
)


_PROGRESS_LINE_RE = re.compile(r'\n\n.{0,60}[：:]\s*\n\n')


def _clean_response(text: str) -> str:
    """Strip leaked tool-call XML/DSML markup and excess whitespace."""
    text = _LEAKED_TOOL_RE.sub('', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    for _ in range(10):
        cleaned = _PROGRESS_LINE_RE.sub('\n\n', text)
        if cleaned == text:
            break
        text = cleaned
    return text.strip()


def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Split text into chunks respecting natural boundaries.

    Tries paragraph breaks first, then newlines, then word boundaries,
    and only falls back to a hard character cut as a last resort.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    min_break = limit // 3
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind('\n\n', min_break, limit)
        if split_at < min_break:
            split_at = text.rfind('\n', min_break, limit)
        if split_at < min_break:
            split_at = text.rfind(' ', min_break, limit)
        if split_at < min_break:
            split_at = limit
        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()
    return chunks


def create_bot(session_manager: "SessionManager") -> TelegramBot:
    """Create a TelegramBot from herandhim.json / env vars."""
    token = config.get_str(
        "channels", "telegram", "token", env="TELEGRAM_BOT_TOKEN",
    )
    if not token:
        raise ValueError("Telegram token not set (env TELEGRAM_BOT_TOKEN or channels.telegram.token)")
    allowed_users = config.get_int_list(
        "channels", "telegram", "allowedUsers", env="TELEGRAM_ALLOWED_USERS",
    )
    require_mention = config.get_bool(
        "channels", "telegram", "requireMention", default=False,
    )
    return TelegramBot(
        session_manager=session_manager,
        token=token,
        allowed_users=allowed_users or None,
        require_mention=require_mention,
    )


# Backward-compatible alias
create_bot_from_env = create_bot
