#!/bin/sh
# ClawSoul container entrypoint.
#
# Materializes /data/claw_soul.json from environment variables on first boot,
# then launches the daemon in the foreground.
#
# Env vars (written to claw_soul.json):
#   CLAW_<PROVIDER>_API_KEY   the only one that's required, e.g.
#                             CLAW_DEEPSEEK_API_KEY — the provider is inferred
#   CLAW_LLM_PROVIDER         optional override, e.g. "deepseek"
#   CLAW_TELEGRAM_TOKEN       your bot token (optional if web-only)
#   CLAW_IMAGE_PROVIDER       optional: gemini|openai|seedream|fal|replicate|
#                             sdwebui|custom — also inferred from whichever
#                             image key is set
#
# See deploy/local/.env.example for the full list.

set -eu

CONFIG_DIR="${CLAWSOUL_HOME:-/data}"
CONFIG_FILE="$CONFIG_DIR/claw_soul.json"

mkdir -p "$CONFIG_DIR"

# Always regenerate config from env vars on boot. Fly secrets are the source
# of truth — user state (memory/photos/persona/.md files) stays on the volume.
echo "[entrypoint] Writing config from env vars → $CONFIG_FILE"
python - <<'PY'
import json, os, pathlib

def env(key, default=""):
    return os.environ.get(key, default)

# provider key -> (default model, default base URL). Keep in sync with
# _OPENAI_COMPATIBLE in claw_soul/main.py. Claude and Gemini use native SDKs
# and take no base URL.
PROVIDERS = {
    "openai":      ("gpt-4o-mini",                             "https://api.openai.com/v1"),
    "openrouter":  ("deepseek/deepseek-chat",                  "https://openrouter.ai/api/v1"),
    "ollama":      ("llama3.1",                                "http://localhost:11434/v1"),
    "lmstudio":    ("local-model",                             "http://localhost:1234/v1"),
    "deepseek":    ("deepseek-chat",                           "https://api.deepseek.com/v1"),
    "grok":        ("grok-3",                                  "https://api.x.ai/v1"),
    "kimi":        ("moonshot-v1-128k",                        "https://api.moonshot.cn/v1"),
    "glm":         ("glm-4-flash",                             "https://open.bigmodel.cn/api/paas/v4/"),
    "qwen":        ("qwen-plus",                               "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "mistral":     ("mistral-large-latest",                    "https://api.mistral.ai/v1"),
    "groq":        ("llama-3.3-70b-versatile",                 "https://api.groq.com/openai/v1"),
    "together":    ("meta-llama/Llama-3.3-70B-Instruct-Turbo", "https://api.together.xyz/v1"),
    "siliconflow": ("deepseek-ai/DeepSeek-V3",                 "https://api.siliconflow.cn/v1"),
    "custom":      ("",                                        ""),
}

# Default to whichever provider actually has a key, so `docker run -e
# CLAW_OPENAI_API_KEY=...` just works without also setting CLAW_LLM_PROVIDER.
provider = env("CLAW_LLM_PROVIDER").lower()
if not provider:
    for name in list(PROVIDERS) + ["claude", "gemini"]:
        if env(f"CLAW_{name.upper()}_API_KEY"):
            provider = name
            break
    else:
        provider = "deepseek"

llm = {"provider": provider}
for name, (model, base) in PROVIDERS.items():
    entry = {
        "apiKey": env(f"CLAW_{name.upper()}_API_KEY"),
        "model":  env(f"CLAW_{name.upper()}_MODEL", model),
    }
    base_url = env(f"CLAW_{name.upper()}_BASE_URL", base)
    if base_url:
        entry["baseUrl"] = base_url
    llm[name] = entry
llm["claude"] = {
    "apiKey": env("CLAW_CLAUDE_API_KEY"),
    "model":  env("CLAW_CLAUDE_MODEL", "claude-sonnet-4-20250514"),
}
llm["gemini"] = {
    "apiKey": env("CLAW_GEMINI_API_KEY"),
    "model":  env("CLAW_GEMINI_MODEL", "gemini-2.0-flash"),
}

# ── Photos ──────────────────────────────────────────────────────────────
# Any of these backends can draw her selfies.  Setting one key is enough:
# the provider is inferred, same as for the LLM.
IMAGE_BACKENDS = {
    "seedream":  ("CLAW_SEEDREAM_API_KEY",  "seedream-5-0-lite-260128",
                  "https://ark.ap-southeast.bytepluses.com/api/v3"),
    "openai":    ("CLAW_IMAGE_OPENAI_KEY",  "gpt-image-1", ""),
    "gemini":    ("CLAW_IMAGE_GEMINI_KEY",  "gemini-2.5-flash-image", ""),
    "fal":       ("CLAW_FAL_KEY",           "fal-ai/flux/schnell", ""),
    "replicate": ("CLAW_REPLICATE_API_TOKEN", "black-forest-labs/flux-schnell", ""),
    "sdwebui":   ("",                       "", "http://localhost:7860"),
    "custom":    ("CLAW_IMAGE_API_KEY",     "", ""),
}

image_provider = env("CLAW_IMAGE_PROVIDER").lower()
if not image_provider:
    for name, (key_env, *_r) in IMAGE_BACKENDS.items():
        if key_env and env(key_env):
            image_provider = name
            break
    else:
        # Vision and image-gen can share one Gemini key, so fall back to it
        # last — an explicitly-set image key always wins.
        if env("CLAW_SDWEBUI_BASE_URL"):
            image_provider = "sdwebui"
        elif env("CLAW_GEMINI_API_KEY"):
            image_provider = "gemini"

skills = {}
for name, (key_env, model, base) in IMAGE_BACKENDS.items():
    entry = {}
    key = env(key_env) if key_env else ""
    if not key and name == "gemini" and image_provider == "gemini":
        key = env("CLAW_GEMINI_API_KEY")
    if key:
        entry["apiKey"] = key
    override = env("CLAW_IMAGE_MODEL") if name == image_provider else ""
    if name == "seedream":
        override = override or env("CLAW_SEEDREAM_MODEL")
    if override or model:
        entry["model"] = override or model
    base_val = env(f"CLAW_{name.upper()}_BASE_URL", base)
    if base_val:
        entry["baseUrl"] = base_val
    skills[name] = entry

if image_provider:
    skills["image"] = {"provider": image_provider}

config = {
    "llm": llm,
    "channels": {
        "telegram": {
            "token": env("CLAW_TELEGRAM_TOKEN"),
            "allowedUsers": [
                int(x) for x in env("CLAW_TELEGRAM_ALLOWED_USERS", "").replace(",", " ").split() if x
            ],
        },
    },
    "skills": skills,
    "deepgram": {"apiKey": env("CLAW_DEEPGRAM_API_KEY")},
    "tavily":   {"apiKey": env("CLAW_TAVILY_API_KEY")},
    "web": {
        "host": "0.0.0.0",
        "port": int(env("PORT", "7788")),
    },
}

path = pathlib.Path(os.environ.get("CONFIG_FILE", "/data/claw_soul.json"))
path.write_text(json.dumps(config, indent=2))
PY

# ── Launch ──────────────────────────────────────────────────────────────
# Single-process daemon: web dashboard + (if a Telegram token is set) the bot.
exec python -m claw_soul --config "$CONFIG_FILE" start --foreground
