#!/bin/sh
# ClawSoul container entrypoint.
#
# Materializes /data/claw_soul.json from environment variables on first boot,
# then launches the daemon in the foreground.
#
# Required CLAW_* env vars (written to claw_soul.json):
#   CLAW_LLM_PROVIDER         e.g. "deepseek"
#   CLAW_<PROVIDER>_API_KEY   e.g. CLAW_DEEPSEEK_API_KEY
#   CLAW_TELEGRAM_TOKEN       your bot token (optional if web-only)
#
# Required SUPABASE_* env vars for the dashboard auth gate (read at runtime,
# NOT written to JSON — secrets stay in the env where they belong):
#   SUPABASE_URL              https://<project>.supabase.co
#   SUPABASE_ANON_KEY         public anon key
#   SUPABASE_JWT_SECRET       JWT signing secret (Supabase project settings)
#   ALLOWED_EMAILS            comma-separated allowlist (e.g. you@example.com)
#
# If SUPABASE_JWT_SECRET is empty, the dashboard runs in OPEN mode (dev only).
#
# Set all of these as Fly secrets:  `fly secrets set KEY=value`

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
    "skills": {
        "seedream": {
            "apiKey": env("CLAW_SEEDREAM_API_KEY"),
            "model":  env("CLAW_SEEDREAM_MODEL", "seedream-5-0-lite-260128"),
            "baseUrl": "https://ark.ap-southeast.bytepluses.com/api/v3",
        },
    },
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
