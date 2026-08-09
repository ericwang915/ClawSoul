"""
Image generation — one interface, several backends.

Photos are how the companion feels physically present, so this shouldn't hinge
on having an account with one specific vendor. Backends, selected by
``skills.image.provider`` (or ``skills.seedream.*`` for the original config):

  seedream       BytePlus Ark / Volcano Engine  (reference-image support)
  openai         gpt-image-1                    (reference via /images/edits)
  gemini         Google — reuses the vision key you already have
  fal            fal.ai — FLUX and friends
  replicate      any Replicate model
  sdwebui        LOCAL: Automatic1111 / Forge / reForge — no key, no upload
  custom         any other OpenAI-compatible /images/generations endpoint

Two response shapes exist in the wild — a URL to fetch, or inline base64 —
so :meth:`generate` normalizes both into ``{"url": …}`` / ``{"b64": …}`` and
:meth:`generate_and_download` writes either to disk.

Reference images (what keeps every selfie the same person) are only supported
by some backends; where they aren't, the request still goes through without one
rather than failing, and we say so in the log.
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

# provider -> (env var, default base URL, default model, supports reference image)
PROVIDERS: dict[str, tuple[str, str, str, bool]] = {
    "seedream":  ("ARK_API_KEY",       DEFAULT_BASE_URL,
                  DEFAULT_MODEL, True),
    "openai":    ("OPENAI_API_KEY",    "https://api.openai.com/v1",
                  "gpt-image-1", True),
    "gemini":    ("GEMINI_API_KEY",    "https://generativelanguage.googleapis.com/v1beta",
                  "gemini-2.5-flash-image", True),
    "fal":       ("FAL_KEY",           "https://fal.run",
                  "fal-ai/flux/schnell", False),
    "replicate": ("REPLICATE_API_TOKEN", "https://api.replicate.com/v1",
                  "black-forest-labs/flux-schnell", False),
    "sdwebui":   ("",                  "http://localhost:7860",
                  "", False),
    "custom":    ("IMAGE_API_KEY",     "", "", True),
}


class SeedreamError(RuntimeError):
    """Image generation failed. (Name kept for backwards compatibility.)"""


ImageGenError = SeedreamError


# ── Config helpers ──────────────────────────────────────────────────────────

def _provider() -> str:
    """Selected backend. Defaults to seedream when its key is set (the
    original behaviour), otherwise the first provider that has a key."""
    explicit = config.get_str("skills", "image", "provider",
                              env="CLAW_IMAGE_PROVIDER", default="").lower()
    if explicit:
        return explicit
    if config.get_str("skills", "seedream", "apiKey", env="ARK_API_KEY"):
        return "seedream"
    for name, (env, *_rest) in PROVIDERS.items():
        if env and config.get_str("skills", name, "apiKey", env=env):
            return name
    return "seedream"


def _cfg(provider: str, field: str, env: str = "", default: str = "") -> str:
    """Read ``skills.<provider>.<field>``, falling back to the legacy
    ``skills.seedream.*`` block so existing installs keep working."""
    val = config.get_str("skills", provider, field, env=env or None, default="")
    if not val and provider == "seedream":
        val = config.get_str("skills", "seedream", field, env=env or None, default="")
    return val or default


def _read_api_key(provider: str | None = None) -> str:
    provider = provider or _provider()
    env, _base, _model, _ref = PROVIDERS.get(provider, PROVIDERS["custom"])
    key = _cfg(provider, "apiKey", env=env)
    needs_key = provider != "sdwebui"
    if not key and needs_key:
        raise SeedreamError(
            f"No API key for image provider '{provider}'. Set "
            f"skills.{provider}.apiKey in claw_soul.json (or {env}), or point "
            f"skills.image.provider at a local backend like 'sdwebui'."
        )
    return key


def _read_base_url(provider: str | None = None) -> str:
    provider = provider or _provider()
    _env, base, _model, _ref = PROVIDERS.get(provider, PROVIDERS["custom"])
    return _cfg(provider, "baseUrl", default=base).rstrip("/")


def _read_default_model(provider: str | None = None) -> str:
    provider = provider or _provider()
    _env, _base, model, _ref = PROVIDERS.get(provider, PROVIDERS["custom"])
    return _cfg(provider, "model", default=model)


def _to_data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _reference_bytes(reference_image: str) -> bytes | None:
    if reference_image and os.path.isfile(reference_image):
        with open(reference_image, "rb") as f:
            return f.read()
    return None


class SeedreamGenerator:
    """Generates images through the configured backend.

    (Name kept for backwards compatibility; ``ImageGenerator`` is an alias.)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        provider: str | None = None,
    ) -> None:
        self.provider = (provider or _provider()).lower()
        self.api_key = api_key if api_key is not None else _read_api_key(self.provider)
        self.base_url = (base_url or _read_base_url(self.provider)).rstrip("/")
        self.model = model or _read_default_model(self.provider)
        self.timeout = timeout

    # ── Generation ──────────────────────────────────────────────────────

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
        """Generate ``n`` images. Returns ``[{"url": …} | {"b64": …}]``.

        ``reference_image`` may be a local path, an http(s) URL, or a data URL;
        backends that can't use one simply generate without it.
        """
        # Content guard — the single chokepoint for every image path. Child
        # sexual content is a strict-liability red line; refuse before spending
        # an API call. Surfaced to the model as a generic safety refusal, which
        # it declines in character (see the agent system prompt).
        from .guard import ImageBlocked, assert_allowed
        try:
            assert_allowed(prompt)
        except ImageBlocked as exc:
            logger.warning("[image] prompt blocked by content guard: %s", exc)
            raise SeedreamError(
                "refused by safety guard — decline this photo warmly and "
                "in-character, do not explain the rule"
            )

        mdl = model or self.model
        _env, _base, _dm, supports_ref = PROVIDERS.get(self.provider,
                                                       PROVIDERS["custom"])
        if reference_image and not supports_ref:
            logger.info("[image] %s has no reference-image support — "
                        "generating without one (face may drift)", self.provider)
            reference_image = None

        logger.info("[image] %s: generating %d image(s) with %s (size=%s, ref=%s)",
                    self.provider, n, mdl, size, bool(reference_image))

        fn = getattr(self, f"_gen_{self.provider}", None) or self._gen_openai_like
        return fn(prompt, size=size, n=n, seed=seed,
                  reference_image=reference_image, model=mdl)

    # ── Backends ────────────────────────────────────────────────────────

    def _post(self, url: str, *, headers: dict, **kw) -> dict:
        r = requests.post(url, headers=headers, timeout=self.timeout, **kw)
        if not r.ok:
            try:
                err = r.json()
                msg = (err.get("error") or {}).get("message") or r.text
            except ValueError:
                msg = r.text[:200]
            raise SeedreamError(f"{self.provider} API {r.status_code}: {msg}")
        return r.json()

    def _gen_openai_like(self, prompt, *, size, n, seed, reference_image, model):
        """Seedream, custom endpoints, and anything else speaking
        POST /images/generations."""
        body: dict[str, Any] = {"model": model, "prompt": prompt, "size": size, "n": n}
        if seed is not None:
            body["seed"] = seed
        if reference_image:
            if reference_image.startswith(("http://", "https://", "data:")):
                body["image"] = reference_image
            elif os.path.isfile(reference_image):
                body["image"] = _to_data_url(reference_image)
            else:
                logger.warning("[image] reference '%s' not found — ignoring.",
                               reference_image)
        data = self._post(
            f"{self.base_url}/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body,
        )
        return self._normalize(data.get("data") or [])

    _gen_seedream = _gen_openai_like
    _gen_custom = _gen_openai_like

    def _gen_openai(self, prompt, *, size, n, seed, reference_image, model):
        """OpenAI gpt-image-1. Returns base64. A reference image switches to
        the /images/edits endpoint, which is what keeps the face consistent."""
        # gpt-image-1 accepts a fixed set of sizes; map ours onto the nearest.
        px = size.split("x")[0]
        osize = "1024x1024" if px == px else "1024x1024"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        ref = _reference_bytes(reference_image) if reference_image else None
        if ref:
            data = self._post(
                f"{self.base_url}/images/edits", headers=headers,
                data={"model": model, "prompt": prompt, "n": str(n), "size": osize},
                files={"image": ("reference.jpg", ref, "image/jpeg")},
            )
        else:
            data = self._post(
                f"{self.base_url}/images/generations",
                headers={**headers, "Content-Type": "application/json"},
                json={"model": model, "prompt": prompt, "n": n, "size": osize},
            )
        return self._normalize(data.get("data") or [])

    def _gen_gemini(self, prompt, *, size, n, seed, reference_image, model):
        """Google image generation. Worth having because the vision key most
        users already configured works here — photos with no extra signup."""
        parts: list[dict] = [{"text": prompt}]
        ref = _reference_bytes(reference_image) if reference_image else None
        if ref:
            parts.append({"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(ref).decode(),
            }})
        data = self._post(
            f"{self.base_url}/models/{model}:generateContent",
            headers={"x-goog-api-key": self.api_key,
                     "Content-Type": "application/json"},
            json={"contents": [{"parts": parts}]},
        )
        out: list[dict[str, Any]] = []
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                blob = part.get("inlineData") or part.get("inline_data") or {}
                if blob.get("data"):
                    out.append({"b64": blob["data"]})
        if not out:
            raise SeedreamError("Gemini returned no image data")
        return out

    def _gen_fal(self, prompt, *, size, n, seed, reference_image, model):
        body: dict[str, Any] = {"prompt": prompt, "num_images": n}
        if seed is not None:
            body["seed"] = seed
        data = self._post(
            f"{self.base_url}/{model}",
            headers={"Authorization": f"Key {self.api_key}",
                     "Content-Type": "application/json"},
            json=body,
        )
        return [{"url": img["url"]} for img in (data.get("images") or []) if img.get("url")]

    def _gen_replicate(self, prompt, *, size, n, seed, reference_image, model):
        body: dict[str, Any] = {"input": {"prompt": prompt, "num_outputs": n}}
        if seed is not None:
            body["input"]["seed"] = seed
        data = self._post(
            f"{self.base_url}/models/{model}/predictions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     # Block until the prediction is done rather than polling.
                     "Prefer": "wait"},
            json=body,
        )
        out = data.get("output")
        urls = out if isinstance(out, list) else ([out] if out else [])
        if not urls:
            raise SeedreamError(f"Replicate returned no output (status={data.get('status')})")
        return [{"url": u} for u in urls if isinstance(u, str)]

    def _gen_sdwebui(self, prompt, *, size, n, seed, reference_image, model):
        """Automatic1111 / Forge / reForge running locally. No key, no upload —
        nothing about the companion's appearance leaves the machine."""
        try:
            w, h = (int(x) for x in size.lower().split("x"))
        except ValueError:
            w = h = 1024
        body: dict[str, Any] = {
            "prompt": prompt, "width": min(w, 1024), "height": min(h, 1024),
            "batch_size": n, "steps": 28,
        }
        if seed is not None:
            body["seed"] = seed
        if model:
            body["override_settings"] = {"sd_model_checkpoint": model}
        data = self._post(f"{self.base_url}/sdapi/v1/txt2img",
                          headers={"Content-Type": "application/json"}, json=body)
        images = data.get("images") or []
        if not images:
            raise SeedreamError("sdwebui returned no images")
        return [{"b64": b} for b in images]

    @staticmethod
    def _normalize(items: list[dict]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in items:
            if it.get("url"):
                out.append({"url": it["url"]})
            elif it.get("b64_json"):
                out.append({"b64": it["b64_json"]})
        if not out:
            raise SeedreamError("Image API returned no usable images")
        return out

    # ── Download ────────────────────────────────────────────────────────

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
        """Generate images and write them to ``output_dir``. Returns paths."""
        items = self.generate(
            prompt, size=size, n=n, seed=seed,
            reference_image=reference_image, model=model,
        )

        os.makedirs(output_dir, exist_ok=True)
        ts = int(time.time())
        paths: list[str] = []
        for idx, item in enumerate(items):
            ext = ".jpg"
            try:
                if item.get("b64"):
                    blob = base64.b64decode(item["b64"])
                    ext = ".png" if blob[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
                else:
                    r = requests.get(item["url"],
                                     headers={"User-Agent": "claw_soul/1.0"},
                                     timeout=self.timeout)
                    r.raise_for_status()
                    blob = r.content
                    ctype = r.headers.get("Content-Type", "")
                    if "png" in ctype:
                        ext = ".png"
                    elif "webp" in ctype:
                        ext = ".webp"
            except Exception as exc:
                logger.error("[image] could not retrieve image %d: %s", idx, exc)
                continue

            suffix = f"_{idx}" if n > 1 else ""
            path = os.path.join(output_dir, f"{filename_prefix}_{ts}{suffix}{ext}")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
            logger.info("[image] saved %s (%.1f KB)", path, len(blob) / 1024)

        if not paths:
            raise SeedreamError("Generation succeeded but no image could be saved.")
        return paths


ImageGenerator = SeedreamGenerator
