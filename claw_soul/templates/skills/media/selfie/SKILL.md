---
name: selfie
description: "Generate and send a selfie of yourself (the AI partner — boyfriend or girlfriend) to the user via Seedream. Use when the user asks for a photo, asks what you're doing right now visually, or you want to share a moment. NOT for: generic image generation (use image_gen), photo editing, or screenshots."
dependencies: requests
metadata:
  emoji: "🤳"
---

# Selfie (AI Partner Self-Portrait)

Take an in-character self-portrait reflecting your current scene (from today's
planner) and your appearance.  The photo is sent via the active channel.

## When to Use

✅ **USE this skill when:**

- "发张自拍" / "拍张照片给我看看"
- "你现在在干嘛，让我看看"
- "今天穿了什么？"
- Spontaneously sharing a moment ("我现在在阳台喝咖啡，看")

## When NOT to Use

❌ **DON'T use this skill when:**

- The user asks for a generic image / illustration → use `image_gen`
- They want a photo of something other than yourself → use `image_gen`
- They want to edit an existing image
- Selfie feature is disabled (`selfie.enabled = false` in config)

## Setup

Requires a BytePlus Ark API key (Seedream).  Configure in `claw_soul.json`:

```json
"skills": {
  "seedream": {
    "apiKey": "YOUR_ARK_API_KEY",
    "model": "seedream-5-0-lite-260128"
  }
}
```

Or set `ARK_API_KEY` environment variable.

**Optional**: drop reference portraits into
`~/.claw_soul/context/photos/reference/` for face consistency, and edit
`~/.claw_soul/context/persona/appearance.md` to lock the visual look.

## Commands

### Take a selfie reflecting the current scene

```bash
python {skill_path}/take_selfie.py
```

### With a scene hint

```bash
python {skill_path}/take_selfie.py --hint "刚画完水彩，举起来给镜头看"
```

### Override model

```bash
python {skill_path}/take_selfie.py --model seedream-5-0-260128
```

## Notes

- Generation takes 5-15 seconds; ~¥0.05-0.10 per photo
- Photos auto-save to `~/.claw_soul/context/photos/` and are pruned after 30 days
- Reference image (if present) is sent inline as a data URL
- Seed is deterministic from the character description, so consistency improves
  even without a reference photo

## Resources

| File | Description |
|------|-------------|
| `take_selfie.py` | Calls `take_selfie()` and sends the result via the active channel |
