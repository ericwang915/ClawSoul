# ClawSoul Router/Scheduler — Phase 2 SaaS deployment

The router is a separate Fly app (`clawsoul-router`) that:

- Receives every user's Telegram webhook at `/telegram/<bot-token>`
- Runs the central APScheduler (proactive / planner / selfie ticks) for paid users
- Wakes per-user worker machines via the Fly Machines API as needed

It is intentionally separate from the legacy `clawsoul` app so the existing
single-process production keeps running while we cut over.

## Required secrets

```bash
fly secrets set \
  SUPABASE_URL=https://<project>.supabase.co \
  SUPABASE_SERVICE_ROLE_KEY=<service-role-key> \
  ROUTER_ADMIN_KEY=<long-random-string>   # used by dashboard → router calls \
  ROUTER_PUBLIC_URL=https://clawsoul-router.fly.dev \
  FLY_API_TOKEN=<fly-api-token>            # for spawning worker machines \
  FLY_WORKER_APP_NAME=clawsoul-worker \
  FLY_WORKER_IMAGE=registry.fly.io/clawsoul-worker:deployment-<tag> \
  FLY_DEFAULT_REGION=sin \
  --app clawsoul-router
```

## First deploy

```bash
# Create the router app + tiny machine
fly apps create clawsoul-router --org personal
fly deploy --config deploy/fly-router/fly.toml --dockerfile deploy/fly-router/Dockerfile --app clawsoul-router

# Create the worker app (no initial machines — they spawn per user)
fly apps create clawsoul-worker --org personal
# Build/push the worker image so FLY_WORKER_IMAGE has something to reference
fly deploy --build-only --image-label initial --app clawsoul-worker \
  --config deploy/fly/fly.toml --dockerfile deploy/fly/Dockerfile
```

The dashboard talks to the router via the `ROUTER_PUBLIC_URL` +
`ROUTER_ADMIN_KEY` secrets it has on the legacy `clawsoul` app.  When a
user saves their Telegram token, the dashboard now POSTs to
`<router>/admin/users/<uid>/provision` + `<router>/admin/users/<uid>/webhook`
instead of hot-adding the bot in its own process.
