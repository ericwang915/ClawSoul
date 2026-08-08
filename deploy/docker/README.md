# Deploying ClawSoul

ClawSoul is a single-process app: a web dashboard, plus a Telegram bot when you
set a token. It stores everything under `/data` (or `~/.claw_soul` outside
Docker). No database, no cloud services.

## Option A — Docker (recommended)

The fastest path. From the repo root:

```bash
cp deploy/local/.env.example deploy/local/.env   # add your DeepSeek key (+ Telegram token, optional)
docker compose -f deploy/local/docker-compose.yml up --build
```

Open http://localhost:7788, finish the browser wizard, and chat. Data persists
in the `clawsoul_data` volume across restarts.

Or run the prebuilt image directly (no clone, no build):

```bash
docker run -e CLAW_DEEPSEEK_API_KEY=sk-... -p 7788:7788 \
  -v clawsoul:/data ghcr.io/ericwang915/clawsoul
```

## Option B — pip

```bash
pip install -e .
claw_soul onboard   # interactive: pick a provider, paste your key, design your companion
claw_soul start     # dashboard at http://localhost:7788
```

## Option C — Fly.io (host your own instance in the cloud)

`fly.toml` runs one always-on instance of your personal ClawSoul.

```bash
fly launch --config deploy/docker/fly.toml --dockerfile deploy/docker/Dockerfile --no-deploy
fly secrets set CLAW_DEEPSEEK_API_KEY=sk-... CLAW_TELEGRAM_TOKEN=123:AA... --app <your-app>
fly deploy --config deploy/docker/fly.toml --dockerfile deploy/docker/Dockerfile --app <your-app>
```

The app name is global on `fly.dev` — change `app = "clawsoul"` in `fly.toml`
if it's taken. `swap_size_mb` gives the small instance headroom for the first
model call.

## Environment variables

See [`deploy/local/.env.example`](../local/.env.example) — every key is
documented there. The only required one is a text-LLM key
(`CLAW_DEEPSEEK_API_KEY`). Everything else (Telegram, Gemini vision, Seedream
selfies, Deepgram voice) is optional and degrades gracefully when unset.
