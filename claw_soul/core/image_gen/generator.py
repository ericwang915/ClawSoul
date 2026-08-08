"""
Seedream image generator — BytePlus Ark / Volcano Engine.

The Seedream API is OpenAI-compatible. We read the API key, base URL, and
model id from config (skills.seedream.*) or from environment variables.

Reference image (for character consistency) is passed via the ``image``
field as a data URL — encoded inline from a local file path so the user
does not need to host their reference image publicly.

Defaults are tuned for selfies: lite model, 2048x2048 (Seedream's minimum).
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import time
from typing import Any

import requests

from ... import config

logger = logging.getLogger(__name__)


# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
DEFAULT_MODEL = "seedream-5-0-lite-260128"
DEFAULT_SIZE = "2048x2048"
DEFAULT_TIMEOUT = 180


class SeedreamError(RuntimeError):
    """Raised when the Seedream API returns an error or invalid response."""


# ── Config plumbing ─────────────────────────────────────────────────────────

def _read_api_key() -> str:
    key = config.get_str("skills", "seedream", "apiKey", env="ARK_API_KEY")
    if not key:
        raise SeedreamError(
            "Seedream API key not configured. Set skills.seedream.apiKey in "
            "claw_soul.json or the ARK_API_KEY environment variable."
        )
    return key


def _read_base_url() -> str:
    return config.get_str(
        "skills", "seedream", "baseUrl",
        env="ARK_BASE_URL",
        default=DEFAULT_BASE_URL,
    ).rstrip("/")


def _read_default_model() -> str:
    return config.get_str(
        "skills", "seedream", "model",
        env="ARK_MODEL",
        default=DEFAULT_MODEL,
    )


# ── Reference image encoding ────────────────────────────────────────────────

def _to_data_url(path: str) -> str:
    """Encode a local image file as a data URL for the ``image`` field."""
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        mime = "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ── Generator ───────────────────────────────────────────────────────────────

class SeedreamGenerator:
    """Thin wrapper around the BytePlus Ark image generation endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key or _read_api_key()
        self.base_url = (base_url or _read_base_url()).rstrip("/")
        self.model = model or _read_default_model()
        self.timeout = timeout

    def generate(
        self,
        prompt: str,
        *,
        size: str = DEFAULT_SIZE,
        n: int = 1,
        seed: int | None = None,
        reference_image: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate ``n`` images. Returns a list of {url, size} dicts.

        ``reference_image`` may be a local file path (will be encoded as
        a data URL) or an http(s) URL passed through unchanged.
        """
        # Content guard — single chokepoint for selfie / candid / cast and any
        # future image path.  Child-sexual content is a strict-liability red
        # line; refuse before spending an API call. Surfaced to the model as a
        # generic safety refusal, which it declines in-character (see agent
        # system prompt).
        from .guard import ImageBlocked, assert_allowed
        try:
            assert_allowed(prompt)
        except ImageBlocked as exc:
            logger.warning("[Seedream] prompt blocked by content guard: %s", exc)
            raise SeedreamError(
                "refused by safety guard — decline this photo warmly and "
                "in-character, do not explain the rule"
            )

        body: dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "size": size,
            "n": n,
        }
        if seed is not None:
            body["seed"] = seed
        if reference_image:
            if reference_image.startswith(("http://", "https://", "data:")):
                body["image"] = reference_image
            elif os.path.isfile(reference_image):
                body["image"] = _to_data_url(reference_image)
            else:
                logger.warning(
                    "[Seedream] reference_image '%s' not found — ignoring.",
                    reference_image,
                )

        url = f"{self.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "[Seedream] generating %d image(s) via %s (size=%s, seed=%s, ref=%s)",
            n, body["model"], size, seed, bool(reference_image),
        )
        resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
        if not resp.ok:
            try:
                err = resp.json().get("error", {})
                msg = err.get("message") or resp.text
                raise SeedreamError(f"Seedream API {resp.status_code}: {msg}")
            except ValueError:
                raise SeedreamError(f"Seedream API {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        items = data.get("data") or []
        if not items:
            raise SeedreamError(f"Seedream returned no images: {data}")
        return items

    def generate_and_download(
        self,
        prompt: str,
        output_dir: str,
        *,
        filename_prefix: str = "img",
        size: str = DEFAULT_SIZE,
        n: int = 1,
        seed: int | None = None,
        reference_image: str | None = None,
        model: str | None = None,
    ) -> list[str]:
        """Generate images and immediately download them to ``output_dir``.

        Returns local file paths.  The Seedream-returned URLs are presigned
        and expire quickly, so we download eagerly.
        """
        items = self.generate(
            prompt,
            size=size,
            n=n,
            seed=seed,
            reference_image=reference_image,
            model=model,
        )

        os.makedirs(output_dir, exist_ok=True)
        ts = int(time.time())
        paths: list[str] = []
        for idx, item in enumerate(items):
            url = item.get("url")
            if not url:
                continue
            ext = ".jpg"
            try:
                r = requests.get(
                    url,
                    headers={"User-Agent": "claw_soul/1.0"},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                blob = r.content
                ctype = r.headers.get("Content-Type", "")
                if "png" in ctype:
                    ext = ".png"
                elif "webp" in ctype:
                    ext = ".webp"
            except Exception as exc:
                logger.error("[Seedream] download failed: %s", exc)
                continue

            suffix = f"_{idx}" if n > 1 else ""
            name = f"{filename_prefix}_{ts}{suffix}{ext}"
            path = os.path.join(output_dir, name)
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
            logger.info("[Seedream] saved %s (%.1f KB)", path, len(blob) / 1024)

        if not paths:
            raise SeedreamError("Seedream succeeded but no images could be downloaded.")
        return paths
