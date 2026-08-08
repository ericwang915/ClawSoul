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

provider = os.environ.get("CLAW_LLM_PROVIDER", "deepseek").lower()

def env(key, default=""):
    return os.environ.get(key, default)

config = {
    "llm": {
        "provider": provider,
        "deepseek": {
            "apiKey": env("CLAW_DEEPSEEK_API_KEY"),
            "model":  env("CLAW_DEEPSEEK_MODEL", "deepseek-chat"),
            "baseUrl": "https://api.deepseek.com/v1",
        },
        "claude": {
            "apiKey": env("CLAW_CLAUDE_API_KEY"),
            "model":  env("CLAW_CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        },
        "gemini": {
            "apiKey": env("CLAW_GEMINI_API_KEY"),
            "model":  env("CLAW_GEMINI_MODEL", "gemini-2.0-flash"),
        },
        "grok": {
            "apiKey": env("CLAW_GROK_API_KEY"),
            "model":  env("CLAW_GROK_MODEL", "grok-3"),
            "baseUrl": "https://api.x.ai/v1",
        },
        "kimi": {
            "apiKey": env("CLAW_KIMI_API_KEY"),
            "model":  env("CLAW_KIMI_MODEL", "moonshot-v1-128k"),
            "baseUrl": "https://api.moonshot.cn/v1",
        },
        "glm": {
            "apiKey": env("CLAW_GLM_API_KEY"),
            "model":  env("CLAW_GLM_MODEL", "glm-4-flash"),
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4/",
        },
    },
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
