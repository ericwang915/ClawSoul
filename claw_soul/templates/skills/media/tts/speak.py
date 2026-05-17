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


def _get_api_key() -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key
    try:
        from claw_soul.config import get as cfg_get
        return cfg_get("elevenlabs", "apiKey") or None
    except ImportError:
        pass
    for path in [
        os.path.expanduser("~/.claw_soul/claw_soul.json"),
        os.path.join(os.getcwd(), "claw_soul.json"),
    ]:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.loads(f.read(), strict=False)
                return cfg.get("elevenlabs", {}).get("apiKey") or None
            except Exception:
                pass
    return None


def tts_elevenlabs(
    text: str,
    output: str,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> bool:
    """Generate speech via ElevenLabs. Returns True on success."""
    key = api_key or _get_api_key()
    if not key:
        print("ElevenLabs API key not found.", file=sys.stderr)
        return False

    url = f"{ELEVENLABS_API}/{voice_id}?output_format=mp3_44100_128"
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
    parser.add_argument("--voice", default=DEFAULT_VOICE_ID, help="ElevenLabs voice ID")
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
