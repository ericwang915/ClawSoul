#!/usr/bin/env python3
"""Text-to-speech via ElevenLabs API (primary) with gTTS fallback."""

import argparse
import json
import os
import ssl
import sys
import urllib.request

ELEVENLABS_API = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel (premade, works on free tier)
DEFAULT_MODEL = "eleven_multilingual_v2"


def _cfg(field: str, env: str = "") -> str | None:
    """Read ``elevenlabs.<field>`` from env, then the loaded config, then the
    config file on disk (skills run as subprocesses without CLAW_* in env)."""
    if env and os.environ.get(env):
        return os.environ[env]
    try:
        from claw_soul.config import get as cfg_get
        val = cfg_get("elevenlabs", field)
        if val:
            return val
    except ImportError:
        pass
    for path in [
        os.path.join(os.environ.get("CLAWSOUL_HOME", ""), "claw_soul.json"),
        os.path.expanduser("~/.claw_soul/claw_soul.json"),
        os.path.join(os.getcwd(), "claw_soul.json"),
    ]:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.loads(f.read(), strict=False)
                val = cfg.get("elevenlabs", {}).get(field)
                if val:
                    return val
            except Exception:
                pass
    return None


def _get_api_key() -> str | None:
    return _cfg("apiKey", env="ELEVENLABS_API_KEY")


def _get_voice_id() -> str:
    """The voice configured in claw_soul.json, else a premade free-tier one."""
    return _cfg("voiceId") or DEFAULT_VOICE_ID


def tts_elevenlabs(
    text: str,
    output: str,
    voice_id: str = "",
    model_id: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> bool:
    """Generate speech via ElevenLabs. Returns True on success."""
    key = api_key or _get_api_key()
    if not key:
        print("ElevenLabs API key not found.", file=sys.stderr)
        return False

    url = f"{ELEVENLABS_API}/{voice_id or _get_voice_id()}?output_format=mp3_44100_128"
    body = json.dumps({
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "xi-api-key": key,
        },
        method="POST",
    )

    try:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except ImportError:
            pass
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            audio = resp.read()
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "wb") as f:
            f.write(audio)
        size_kb = len(audio) / 1024
        print(f"Saved: {output} (ElevenLabs, {size_kb:.1f} KB)")
        return True
    except Exception as exc:
        print(f"ElevenLabs TTS failed: {exc}", file=sys.stderr)
        return False


def tts_gtts(text: str, lang: str, slow: bool, output: str) -> bool:
    """Fallback TTS via gTTS."""
    try:
        from gtts import gTTS
    except ImportError:
        print("gTTS not installed. Run: pip install gTTS", file=sys.stderr)
        return False
    tts = gTTS(text=text, lang=lang, slow=slow)
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    tts.save(output)
    print(f"Saved: {output} (gTTS, lang={lang})")
    return True


def main():
    parser = argparse.ArgumentParser(description="Text-to-speech (ElevenLabs + gTTS fallback)")
    parser.add_argument("text", help="Text to speak")
    parser.add_argument("--engine", default="elevenlabs", choices=["elevenlabs", "gtts"])
    parser.add_argument("--voice", default="",
                        help="ElevenLabs voice ID (default: elevenlabs.voiceId in config)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="ElevenLabs model ID")
    parser.add_argument("--lang", default="zh", help="Language code for gTTS fallback")
    parser.add_argument("--slow", action="store_true", help="Slow speech (gTTS only)")
    parser.add_argument("--output", "-o", default="voice.mp3", help="Output file path")
    args = parser.parse_args()

    if args.engine == "elevenlabs":
        ok = tts_elevenlabs(args.text, args.output, args.voice, args.model)
        if not ok:
            print("Falling back to gTTS...", file=sys.stderr)
            tts_gtts(args.text, args.lang, args.slow, args.output)
    else:
        tts_gtts(args.text, args.lang, args.slow, args.output)


if __name__ == "__main__":
    main()
