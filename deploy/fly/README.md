# ClawSoul on Fly.io — single-tenant deploy

Your personal ClawSoul daemon running 24/7 in the cloud instead of on your laptop.
One Fly app, one machine, one persistent volume for `~/.claw_soul/` state.

This is **phase 1** — see [Future: multi-tenant SaaS](#future-multi-tenant-saas) at the bottom for the path to one-instance-per-paying-user.

## What's here

| File | Purpose |
|---|---|
| `Dockerfile` | python:3.12-slim, multi-stage build, ffmpeg for voice transcription, non-root user, /data as volume mount |
| `entrypoint.sh` | First-boot: writes `/data/claw_soul.json` from `CLAW_*` env vars (set as Fly secrets). Then runs the daemon in foreground. |
| `fly.toml` | App config: shared-cpu-1x 512MB, always-on, /api/status health check, 1GB volume |
| `.dockerignore` | Excludes frontend/, tests/, local state |

## Prerequisites

```bash
# Fly CLI
brew install flyctl
fly auth login

# Docker Desktop (only needed if you want to test the image locally first)
open -a Docker
```

## Deploy from scratch (one-time)

Run these from the **project root**, not from `deploy/fly/`:

```bash
# 1. Create the Fly app (the name "clawsoul" is global on fly.dev — if it's
#    taken, pick another and update fly.toml accordingly)
fly apps create clawsoul --org personal

# 2. Create the persistent volume in the same region as fly.toml's primary_region
fly volumes create claw_data --region sin --size 1 --app clawsoul --yes

# 3a. Create a Supabase project (free tier is enough):
#     - https://supabase.com → New project
#     - Settings → API → copy "Project URL" + "anon public" key
#     - Settings → API → JWT Settings → reveal the "JWT Secret"
#     - Auth → Providers → enable Email (default)
#     - Auth → URL Configuration → add https://clawsoul.fly.dev to allowed URLs
#
# 3b. Set ALL secrets — the auth gate REFUSES to start unless SUPABASE_JWT_SECRET
#     is set, otherwise the dashboard would be open to the public internet.
fly secrets set \
  CLAW_LLM_PROVIDER=deepseek \
  CLAW_DEEPSEEK_API_KEY=sk-... \
  CLAW_TELEGRAM_TOKEN=12345:abc... \
  CLAW_TELEGRAM_ALLOWED_USERS=12345678 \
  SUPABASE_URL=https://xxx.supabase.co \
  SUPABASE_ANON_KEY=eyJhbGci... \
  SUPABASE_JWT_SECRET=super-secret-jwt-string \
  ALLOWED_EMAILS=you@example.com \
  --app clawsoul

# 3c. Create your dashboard user (one-time, via Supabase dashboard):
#     - Authentication → Users → "Add user" → Create new user
#     - Use the email you listed in ALLOWED_EMAILS, pick a password
#     - Confirm the user (toggle "Auto Confirm" or click the email link)

# Optional secrets — only set if you want these features
fly secrets set CLAW_SEEDREAM_API_KEY=... --app clawsoul  # AI selfies
fly secrets set CLAW_DEEPGRAM_API_KEY=... --app clawsoul  # voice input
fly secrets set CLAW_TAVILY_API_KEY=...   --app clawsoul  # web search

# 4. Build and deploy
fly deploy --config deploy/fly/fly.toml --dockerfile deploy/fly/Dockerfile --app clawsoul
```

That last step:
- Builds the Docker image on Fly's builder
- Pushes to `registry.fly.io/clawsoul`
- Creates one machine, attaches the volume, starts the daemon
- Returns the public URL: `https://clawsoul.fly.dev`

## After deploy

```bash
# Dashboard
open https://clawsoul.fly.dev

# Live logs
fly logs --app clawsoul

# SSH into the machine
fly ssh console --app clawsoul

# Inside the container, your state is at /data:
ls -la /data/   # claw_soul.json, context/, memory/, photos/...
```

## Day-to-day operations

### Deploying changes

```bash
# After editing claw_soul/ code
fly deploy --config deploy/fly/fly.toml --dockerfile deploy/fly/Dockerfile --app clawsoul
```

The volume persists across deploys, so your memory/photos/config aren't lost.

### Rotating an API key

```bash
fly secrets set CLAW_DEEPSEEK_API_KEY=new-key --app clawsoul
# Fly auto-restarts the machine. NOTE: entrypoint.sh skips re-writing config
# if /data/claw_soul.json already exists, so to pick up rotated secrets you
# need to either:
#   a) SSH in and delete /data/claw_soul.json before restart, OR
#   b) Edit /data/claw_soul.json directly via SSH
```

### Connecting your domain

```bash
# In Cloudflare dashboard:
#   - Add CNAME: bot.herandhim.ai → clawsoul.fly.dev (proxy OFF)

# Then in Fly:
fly certs add bot.herandhim.ai --app clawsoul
# Wait ~30s for Let's Encrypt cert to issue
```

### Rolling back

```bash
fly releases --app clawsoul                # list past deploys
fly releases rollback <version> --app clawsoul
```

### Stopping (to save money temporarily)

```bash
fly scale count 0 --app clawsoul    # stop the machine
fly scale count 1 --app clawsoul    # start it back up
```

## Cost (single user, always-on)

| Item | Monthly |
|---|---|
| shared-cpu-1x, 512MB, always-on | ~$3.90 |
| 1GB volume | $0.15 |
| Bandwidth (low) | ~$0 |
| **Total** | **~$4/mo** |

If you switch Telegram from polling to webhook and enable `auto_stop_machines = "stop"`, this drops to <$1/mo.

## Troubleshooting

**Machine won't start**
```bash
fly logs --app clawsoul                            # check daemon stderr
fly machine list --app clawsoul                    # see machine state
fly ssh console --app clawsoul                     # shell in
```

**Health check failing**
The check hits `/api/status`. If it 500s during startup, look at logs for LLM provider init errors (usually means a secret is missing or wrong).

**"Volume not found"**
Make sure the volume name in `fly.toml` (`claw_data`) matches what you created with `fly volumes create`. They must be in the same region as `primary_region`.

**Image build too slow**
The numpy/scikit-learn install is the bottleneck (~2-3 min). Consider switching ClawSoul to BM25-only retrieval if cold builds annoy you, or just rely on Fly's layer caching (subsequent builds are <30s).

## Future: multi-tenant SaaS

When you're ready to let strangers sign up and each gets their own ClawSoul:

| What changes | Why |
|---|---|
| Switch Telegram bot to webhook mode | So machines can auto-stop without missing messages |
| Storage abstraction layer (`StorageBackend`) | Currently all paths assume `~/.claw_soul/` — refactor to namespace by `user_id` |
| Migrate markdown memory + BM25 indexes to Postgres + pgvector | Shared multi-tenant DB beats per-user filesystem |
| Migrate photos to R2/S3 | Volumes don't scale to thousands of users |
| Add control plane (signup, payment, user→config mapping) | The actual SaaS bit |

At that point you have two options for the daemon tier:
- **Shared cluster** (recommended): a few Fly machines handle requests for all users, looking up state in shared Postgres. Cheapest at scale (~$0.10/user/mo).
- **One machine per user**: keeps single-user code unchanged but costs 10x. See `git log` for the earlier scaffold of this pattern if you want to revive it.
