---
name: image_gen
description: "Generate images via OpenAI Images API (DALL-E 3, GPT-image). Use when: user asks to generate, create, draw, or design an image, illustration, logo, or artwork. NOT for: editing existing images, image format conversion, or screenshots."
dependencies: requests
metadata:
  emoji: "🖼️"
---

# Image Generation

Generate images via the OpenAI Images API.

## When to Use

✅ **USE this skill when:**

- "Generate an image of a sunset over mountains"
- "Create a logo for my project"
- "Draw a cartoon cat wearing a hat"
- "Design a banner for my blog"
- "Make me an illustration of X"

## When NOT to Use

❌ **DON'T use this skill when:**

- Editing or modifying existing images → use image editing tools
- Screenshot capture → use system screenshot tools
- Image format conversion → use `run_command` with ImageMagick/Pillow
- Web image search → use `web_search` tool

## Setup

Requires an OpenAI API key with Images API access.

Configure in `claw_soul.json`:

```json
"skills": {
  "image_gen": {
    "apiKey": "sk-your-openai-key"
  }
}
```

Or set `OPENAI_API_KEY` environment variable.

## Commands

### Generate an image

```bash
python {skill_path}/generate.py "a serene mountain lake at sunset, watercolor style"
```

### Options

```bash
# Specific model
python {skill_path}/generate.py "prompt" --model dall-e-3

# Size options
python {skill_path}/generate.py "prompt" --size 1792x1024

# Quality
python {skill_path}/generate.py "prompt" --quality hd

# Style (dall-e-3 only)
python {skill_path}/generate.py "prompt" --style vivid

# Output directory
python {skill_path}/generate.py "prompt" --output ./images/
```

### Model-Specific Parameters

**DALL-E 3:**
- Sizes: `1024x1024`, `1792x1024`, `1024x1792`
- Quality: `standard`, `hd`
- Style: `vivid`, `natural`
- Generates 1 image at a time

**DALL-E 2:**
- Sizes: `256x256`, `512x512`, `1024x1024`
- Quality: `standard` only
- Can generate multiple images

## Notes

- Output is saved as PNG files in the specified directory (default: current dir)
- Each generation costs API credits (DALL-E 3 HD ~$0.08/image)
- Prompts are sometimes rewritten by the API for safety
- Use `--style natural` for more photographic results

## Resources

| File | Description |
|------|-------------|
| `generate.py` | OpenAI Images API wrapper |
