"""
Speech-to-text via Deepgram.

Provides both sync and async helpers so every channel can call
``transcribe_audio`` without worrying about event-loop differences.

Returns the transcript string on success, or ``None`` when the Deepgram
API key is not configured.

Language is configurable via ``deepgram.language`` in herandhim.json:
  - ``"auto"`` (default) — auto-detect, with fallback retries for short clips
  - ``"zh"``/``"en"``/``"ja"``/… — force a specific language
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEEPGRAM_BASE = "https://api.deepgram.com/v1/listen"

_FALLBACK_LANGUAGES = ("zh", "en", "ja", "ko", "es", "fr", "de")

_NO_KEY_MSG = (
    "Voice messages are not enabled yet.\n\n"
    "To unlock voice input, you need a Deepgram API key:\n"
    "1. Go to https://console.deepgram.com/signup and create a free account\n"
    "2. After signing in, go to API Keys (left sidebar)\n"
    "3. Click \"Create a New API Key\", give it a name, and copy the key\n"
    "4. Set it in Config -> deepgram -> apiKey (or set the DEEPGRAM_API_KEY env var)\n\n"
    "Deepgram offers $200 free credits on signup — no credit card required."
)


def _get_key() -> str | None:
    from .. import config
    return config.get("deepgram", "apiKey", env="DEEPGRAM_API_KEY") or None


def _get_config_language() -> str:
    from .. import config
    return config.get_str("deepgram", "language") or "auto"


def _get_model() -> str:
    from .. import config
    return config.get_str("deepgram", "model") or "nova-2"


def _build_url(language: str | None = None) -> str:
    """Build the Deepgram API URL.

    If *language* is given, use it directly (e.g. ``"zh"``).
    If ``None``, read from config (default ``"auto"`` → detect_language).
    """
    model = _get_model()
    lang = language or _get_config_language()

    params = [f"model={model}", "smart_format=true", "punctuate=true"]

    if lang == "auto":
        params.append("detect_language=true")
    else:
        params.append(f"language={lang}")

    return f"{_DEEPGRAM_BASE}?{'&'.join(params)}"


def _headers(key: str, content_type: str) -> dict[str, str]:
    return {
        "Authorization": f"Token {key}",
        "Content-Type": content_type,
    }


# ── Async ─────────────────────────────────────────────────────────────────────

async def transcribe_bytes_async(
    audio: bytes, content_type: str = "audio/ogg"
) -> str | None:
    """Non-blocking transcription with automatic fallback for short clips.

    Returns the transcript text, or ``None`` if no Deepgram key is set.
    """
    key = _get_key()
    if not key:
        return None

    cfg_lang = _get_config_language()

    if cfg_lang != "auto":
        return await _call_async(key, audio, content_type, language=cfg_lang)

    transcript = await _call_async(key, audio, content_type, language=None)
    if transcript:
        return transcript

    for lang in _FALLBACK_LANGUAGES:
        transcript = await _call_async(key, audio, content_type, language=lang)
        if transcript:
            logger.info("[STT] Fallback to language=%s succeeded", lang)
            return transcript

    logger.warning("[STT] All fallback languages returned empty (bytes=%d)", len(audio))
    return ""


async def _call_async(
    key: str, audio: bytes, content_type: str, language: str | None
) -> str:
    import httpx

    url = _build_url(language=language or "auto")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url, content=audio,
            headers=_headers(key, content_type),
        )
        resp.raise_for_status()
        return _extract_transcript(resp.json())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_transcript(data: dict) -> str:
    try:
        return (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        )
    except (IndexError, KeyError):
        return ""


def no_key_message() -> str:
    """User-facing message when Deepgram key is missing."""
    return _NO_KEY_MSG
