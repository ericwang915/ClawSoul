"""
Image generation — one interface, several backends.

Photos are how the companion feels physically present, so this shouldn't hinge
on having an account with one specific vendor. Backends, selected by
``skills.image.provider`` (or ``skills.seedream.*`` for the original config):

  seedream       BytePlus Ark / Volcano Engine  (reference-image support)
  openai         gpt-image-1                    (reference via /images/edits)
  gemini         Google — reuses the vision key you already have
  openrouter     reuses the LLM key from the quickstart — no extra signup
  bfl            Black Forest Labs — FLUX.1 Kontext, built for keeping one
                 character's face across shots
  fal            fal.ai — FLUX and friends
  replicate      any Replicate model
  stability      Stability AI — Stable Image Core / Ultra / SD3.5
  dashscope      Alibaba Qwen / Wan — reuses a Qwen LLM key
  sdwebui        LOCAL: Automatic1111 / Forge / reForge — no key, no upload
  comfyui        LOCAL: ComfyUI, incl. your own saved workflow graph
  pollinations   free and keyless — photos work before you sign up anywhere
  custom         any other OpenAI-compatible /images/generations endpoint

Aggregators that speak the OpenAI image API (Together, DeepInfra, Novita,
SiliconFlow, Fireworks…) need no code of their own — point ``custom`` at them.

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
from urllib.parse import quote

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
    "seedream":     ("ARK_API_KEY",         DEFAULT_BASE_URL,
                     DEFAULT_MODEL, True),
    "openai":       ("OPENAI_API_KEY",      "https://api.openai.com/v1",
                     "gpt-image-1", True),
    "gemini":       ("GEMINI_API_KEY",      "https://generativelanguage.googleapis.com/v1beta",
                     "gemini-2.5-flash-image", True),
    "openrouter":   ("OPENROUTER_API_KEY",  "https://openrouter.ai/api/v1",
                     "google/gemini-2.5-flash-image", True),
    "bfl":          ("BFL_API_KEY",         "https://api.bfl.ai/v1",
                     "flux-kontext-pro", True),
    "fal":          ("FAL_KEY",             "https://fal.run",
                     "fal-ai/flux/schnell", False),
    "replicate":    ("REPLICATE_API_TOKEN", "https://api.replicate.com/v1",
                     "black-forest-labs/flux-schnell", False),
    "stability":    ("STABILITY_API_KEY",   "https://api.stability.ai",
                     "core", False),
    "dashscope":    ("DASHSCOPE_API_KEY",   "https://dashscope.aliyuncs.com/api/v1",
                     "wan2.2-t2i-flash", False),
    "sdwebui":      ("",                    "http://localhost:7860",
                     "", False),
    "comfyui":      ("",                    "http://localhost:8188",
                     "", False),
    "pollinations": ("",                    "https://image.pollinations.ai",
                     "flux", False),
    "custom":       ("IMAGE_API_KEY",       "", "", True),
}

# Backends that run locally or need no account.
KEYLESS = {"sdwebui", "comfyui", "pollinations"}


class SeedreamError(RuntimeError):
    """Image generation failed. (Name kept for backwards compatibility.)"""


ImageGenError = SeedreamError


# ── Config helpers ──────────────────────────────────────────────────────────

def _provider() -> str:
    """Selected backend. Defaults to seedream when its key is set (the
    original behaviour), otherwise the first provider that has a key."""
    explicit = config.get_str("skills", "image", "provider",
                              env="HERANDHIM_IMAGE_PROVIDER", default="").lower()
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
    if not key and provider not in KEYLESS:
        raise SeedreamError(
            f"No API key for image provider '{provider}'. Set "
            f"skills.{provider}.apiKey in herandhim.json (or {env}), or point "
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


def _dimensions(size: str, fallback: int = 1024) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in size.lower().split("x"))
        return w, h
    except (ValueError, TypeError):
        return fallback, fallback


def _openai_size(size: str) -> str:
    """gpt-image-1 only accepts three sizes; pick the one matching our aspect
    ratio so portrait selfies don't get squared off."""
    w, h = _dimensions(size)
    if w > h * 1.15:
        return "1536x1024"
    if h > w * 1.15:
        return "1024x1536"
    return "1024x1024"


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

    def _get(self, url: str, *, headers: dict | None = None, **kw):
        r = requests.get(url, headers=headers or {}, timeout=self.timeout, **kw)
        if not r.ok:
            raise SeedreamError(f"{self.provider} API {r.status_code}: {r.text[:200]}")
        return r

    def _poll(self, url: str, *, headers: dict, done, every: float = 1.5):
        """Wait on an async job. ``done(payload)`` returns the finished payload
        or None to keep waiting. Bounded by this generator's timeout so a stuck
        job can't hang the chat loop forever."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            payload = self._get(url, headers=headers).json()
            finished = done(payload)
            if finished is not None:
                return finished
            time.sleep(every)
        raise SeedreamError(f"{self.provider}: image job timed out")

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
        osize = _openai_size(size)
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

    def _gen_openrouter(self, prompt, *, size, n, seed, reference_image, model):
        """OpenRouter routes image models through the chat API. Worth having
        because the quickstart in the README already hands out this key — the
        cheapest possible path from 'it talks' to 'it sends photos'."""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        ref = _reference_bytes(reference_image) if reference_image else None
        if ref:
            content.append({"type": "image_url", "image_url": {
                "url": f"data:image/jpeg;base64,{base64.b64encode(ref).decode()}"}})
        data = self._post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/ericwang915/HerAndHim",
                     "X-Title": "HerAndHim"},
            json={"model": model, "modalities": ["image", "text"],
                  "messages": [{"role": "user", "content": content}]},
        )
        out: list[dict[str, Any]] = []
        for choice in data.get("choices") or []:
            for img in (choice.get("message") or {}).get("images") or []:
                url = (img.get("image_url") or {}).get("url") or img.get("url")
                if not url:
                    continue
                # Image models here answer with a data URL far more often
                # than a fetchable one; unwrap so callers see plain base64.
                if url.startswith("data:"):
                    out.append({"b64": url.split(",", 1)[-1]})
                else:
                    out.append({"url": url})
        if not out:
            raise SeedreamError(
                f"OpenRouter returned no image — is '{model}' an image model?")
        return out

    def _gen_bfl(self, prompt, *, size, n, seed, reference_image, model):
        """Black Forest Labs. FLUX.1 Kontext takes a reference image as the
        subject to preserve, which is the closest thing to a real answer for
        'why isn't this the same person'. Async: submit, then poll."""
        w, h = _dimensions(size)
        body: dict[str, Any] = {"prompt": prompt, "width": w, "height": h,
                                "output_format": "jpeg"}
        if seed is not None:
            body["seed"] = seed
        ref = _reference_bytes(reference_image) if reference_image else None
        if ref:
            body["input_image"] = base64.b64encode(ref).decode()
        headers = {"x-key": self.api_key, "Content-Type": "application/json"}
        job = self._post(f"{self.base_url}/{model}", headers=headers, json=body)
        poll_url = job.get("polling_url") or f"{self.base_url}/get_result?id={job.get('id')}"

        def done(payload):
            status = payload.get("status")
            if status == "Ready":
                return payload
            if status in ("Error", "Failed", "Content Moderated",
                          "Request Moderated"):
                raise SeedreamError(f"BFL job failed: {status}")
            return None

        result = self._poll(poll_url, headers={"x-key": self.api_key}, done=done)
        sample = (result.get("result") or {}).get("sample")
        if not sample:
            raise SeedreamError("BFL returned no image")
        return [{"url": sample}]

    def _gen_stability(self, prompt, *, size, n, seed, reference_image, model):
        """Stability AI. Asking for JSON rather than raw bytes keeps this on
        the same base64 path as every other backend."""
        w, h = _dimensions(size)
        fields: dict[str, Any] = {
            "prompt": (None, prompt),
            "output_format": (None, "jpeg"),
            "aspect_ratio": (None, "1:1" if w == h else ("16:9" if w > h else "9:16")),
        }
        if seed is not None:
            fields["seed"] = (None, str(seed))
        data = self._post(
            f"{self.base_url}/v2beta/stable-image/generate/{model}",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Accept": "application/json"},
            files=fields,
        )
        if not data.get("image"):
            raise SeedreamError(
                f"Stability returned no image (finish_reason={data.get('finish_reason')})")
        return [{"b64": data["image"]}]

    def _gen_dashscope(self, prompt, *, size, n, seed, reference_image, model):
        """Alibaba Qwen / Wan. Here because a Qwen LLM key — already one of the
        supported chat providers — also generates images. Async: submit, poll."""
        w, h = _dimensions(size)
        params: dict[str, Any] = {"n": n, "size": f"{w}*{h}"}
        if seed is not None:
            params["seed"] = seed
        job = self._post(
            f"{self.base_url}/services/aigc/text2image/image-synthesis",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     "X-DashScope-Async": "enable"},
            json={"model": model, "input": {"prompt": prompt}, "parameters": params},
        )
        task_id = (job.get("output") or {}).get("task_id")
        if not task_id:
            raise SeedreamError(f"DashScope did not return a task id: {job}")

        def done(payload):
            status = (payload.get("output") or {}).get("task_status")
            if status == "SUCCEEDED":
                return payload
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise SeedreamError(
                    f"DashScope task {status}: "
                    f"{(payload.get('output') or {}).get('message', '')}")
            return None

        result = self._poll(f"{self.base_url}/tasks/{task_id}",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            done=done)
        urls = [r["url"] for r in (result.get("output") or {}).get("results") or []
                if r.get("url")]
        if not urls:
            raise SeedreamError("DashScope returned no image URLs")
        return [{"url": u} for u in urls]

    def _gen_pollinations(self, prompt, *, size, n, seed, reference_image, model):
        """Free and keyless. Its real job is removing the last excuse not to
        try photos — you can see what she looks like before signing up for
        anything. Rate-limited and lower fidelity; not the one to settle on."""
        w, h = _dimensions(size)
        params = {"width": min(w, 1024), "height": min(h, 1024),
                  "model": model or "flux", "nologo": "true"}
        if seed is not None:
            params["seed"] = seed
        r = self._get(f"{self.base_url}/prompt/{quote(prompt, safe='')}",
                      headers={"User-Agent": "herandhim/1.0"}, params=params)
        if not r.content or not r.headers.get("Content-Type", "").startswith("image"):
            raise SeedreamError("Pollinations returned no image")
        return [{"b64": base64.b64encode(r.content).decode()}]

    # Minimal SDXL/SD txt2img graph. Users with a tuned pipeline point
    # skills.comfyui.workflow at their own exported API-format JSON instead.
    _COMFY_WORKFLOW: dict[str, Any] = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": 0, "steps": 28, "cfg": 7.0, "sampler_name": "dpmpp_2m",
            "scheduler": "karras", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0],
            "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "%model%"}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "%prompt%", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "%negative%", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "HerAndHim", "images": ["8", 0]}},
    }

    def _gen_comfyui(self, prompt, *, size, n, seed, reference_image, model):
        """ComfyUI running locally — where most self-hosted image generation
        actually happens now. Like sdwebui, nothing leaves the machine, but
        this one can run whatever workflow you've already tuned."""
        import copy
        import json as _json

        w, h = _dimensions(size)
        graph = copy.deepcopy(self._COMFY_WORKFLOW)
        custom = _cfg("comfyui", "workflow")
        if custom:
            if not os.path.isfile(custom):
                raise SeedreamError(f"ComfyUI workflow not found: {custom}")
            with open(custom) as f:
                graph = _json.load(f)

        # Substitute into whichever graph we ended up with — placeholders let
        # a hand-tuned workflow take the same inputs as the built-in one.
        subs = {
            "%prompt%": prompt,
            "%negative%": _cfg("comfyui", "negativePrompt",
                               default="text, watermark, logo, caption, subtitles, "
                                       "signature, lowres, deformed"),
            "%model%": model or _cfg("comfyui", "checkpoint",
                                     default="sd_xl_base_1.0.safetensors"),
            "%width%": w, "%height%": h, "%seed%": seed if seed is not None else 0,
        }
        for node in graph.values():
            for field, val in (node.get("inputs") or {}).items():
                if isinstance(val, str) and val in subs:
                    node["inputs"][field] = subs[val]
            cls = node.get("class_type")
            if cls == "EmptyLatentImage" and not custom:
                node["inputs"].update({"width": w, "height": h, "batch_size": n})
            elif cls == "KSampler" and seed is not None and not custom:
                node["inputs"]["seed"] = seed

        submitted = self._post(f"{self.base_url}/prompt",
                               headers={"Content-Type": "application/json"},
                               json={"prompt": graph})
        pid = submitted.get("prompt_id")
        if not pid:
            raise SeedreamError(f"ComfyUI rejected the workflow: {submitted}")

        history = self._poll(
            f"{self.base_url}/history/{pid}", headers={},
            done=lambda payload: payload.get(pid) if payload.get(pid) else None,
        )

        out: list[dict[str, Any]] = []
        for node_out in (history.get("outputs") or {}).values():
            for img in node_out.get("images") or []:
                blob = self._get(f"{self.base_url}/view", params={
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }).content
                out.append({"b64": base64.b64encode(blob).decode()})
        if not out:
            raise SeedreamError("ComfyUI produced no images — check the workflow")
        return out

    def _gen_sdwebui(self, prompt, *, size, n, seed, reference_image, model):
        """Automatic1111 / Forge / reForge running locally. No key, no upload —
        nothing about the companion's appearance leaves the machine."""
        w, h = _dimensions(size)
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
                                     headers={"User-Agent": "herandhim/1.0"},
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
