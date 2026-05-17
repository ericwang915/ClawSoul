#!/usr/bin/env python3
"""Generate images via the OpenAI Images API."""
import argparse
import base64
import json
import os
import sys
from datetime import datetime

try:
    import requests
except ImportError:
    print("Error: requests is not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


def _get_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return key
    for path in [os.path.expanduser("~/.claw_soul/claw_soul.json"), "claw_soul.json"]:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    cfg = json.load(f)
                key = cfg.get("skills", {}).get("image_gen", {}).get("apiKey", "")
                if key:
                    return key
            except (json.JSONDecodeError, OSError):
                continue
    print("Error: OpenAI API key not configured. Set skills.image_gen.apiKey or OPENAI_API_KEY", file=sys.stderr)
    sys.exit(1)


def generate(prompt: str, model: str, size: str, quality: str,
             style: str | None, output_dir: str) -> str:
    api_key = _get_api_key()

    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "response_format": "b64_json",
    }
    if style and model == "dall-e-3":
        body["style"] = style

    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"image_{timestamp}.png"
    filepath = os.path.join(output_dir, filename)

    image_data = data["data"][0]
    b64 = image_data.get("b64_json", "")
    if b64:
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(b64))
    else:
        url = image_data.get("url", "")
        if url:
            img_resp = requests.get(url, timeout=30)
            with open(filepath, "wb") as f:
                f.write(img_resp.content)

    revised_prompt = image_data.get("revised_prompt", "")
    return filepath, revised_prompt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Image generation prompt")
    parser.add_argument("--model", default="dall-e-3", choices=["dall-e-2", "dall-e-3"])
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", default="standard", choices=["standard", "hd"])
    parser.add_argument("--style", default=None, choices=["vivid", "natural"])
    parser.add_argument("--output", default=".", help="Output directory")
    args = parser.parse_args()

    try:
        filepath, revised = generate(
            args.prompt, args.model, args.size, args.quality, args.style, args.output
        )
        print(f"Image saved: {filepath}")
        if revised:
            print(f"Revised prompt: {revised}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
