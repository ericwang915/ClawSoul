---
name: tts
description: "Text-to-speech — convert text to voice message using ElevenLabs. Use when: user asks for a voice message, wants to hear something spoken, or you want to send a voice reply instead of text. Also use when you feel like expressing emotion through voice."
dependencies: null
metadata:
  emoji: "🎙️"
---

# Text-to-Speech (ElevenLabs)

Convert text to a natural voice message and send it as an audio file.

## When to Use

✅ **USE this skill when:**

- You want to send a voice message instead of text (偶尔用，增加亲密感)
- User asks "发个语音" or "say it"
- You want to express strong emotion (excited, whiny, loving)
- Saying goodnight or good morning with warmth
- Singing a line or doing a cute impression

❌ **DON'T use this skill when:**

- Normal text chat is fine
- Don't overuse — voice messages are special, not every reply

## Usage

```bash
python {skill_path}/speak.py "想你了宝贝～晚安" --output voice.mp3
```

### Options

```bash
# Custom voice ID
python {skill_path}/speak.py "早安呀" --voice ByhETIclHirOlWnWKhHc --output voice.mp3

# Fallback to gTTS if ElevenLabs is unavailable
python {skill_path}/speak.py "你好" --engine gtts --lang zh --output voice.mp3
```

## Configuration

Set your ElevenLabs API key in `claw_soul.json`:

```json
{
  "elevenlabs": {
    "apiKey": "sk_...",
    "voiceId": "ByhETIclHirOlWnWKhHc"
  }
}
```

Or via environment variable: `ELEVENLABS_API_KEY`

## Notes

- Uses ElevenLabs `eleven_multilingual_v2` model (supports Chinese + English)
- Default voice: `ByhETIclHirOlWnWKhHc`
- Falls back to gTTS (Google) if ElevenLabs fails
- Output is MP3 format
- After generating, use the file sender to deliver it to the user

## Resources

| File | Description |
|------|-------------|
| `speak.py` | ElevenLabs TTS with gTTS fallback |
