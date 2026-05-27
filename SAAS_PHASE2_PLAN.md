# ClawSoul SaaS Phase 2 — Container-per-user + Central Scheduler

This document captures the architectural shift from "one Fly machine, all
users in one process" to **"one Fly machine per active user, woken on
demand by a central router/scheduler"** — the model recommended for
AI-companion SaaS where users need always-feeling-present partners but
most users are idle most of the time.

Status: **Design** — not yet implemented. Phase 2a-f land incrementally.

---

## Goals

1. **Cost-scale**: 1000 idle users should cost <$300/month, not $5,000.
2. **Isolation**: one runaway user can't OOM, slow, or read another.
3. **Preserve proactivity**: paid users still get scheduled selfies +
   proactive messages even when their machine is asleep.
4. **Zero-downtime migration**: the current single-process production
   keeps running while we build alongside.

---

## Topology

```
                  Telegram                Dashboard / WS
                     │                          │
                     ▼                          ▼
        ┌───────────────────────────────────────────────────┐
        │   Router / Scheduler service (always-on)          │
        │   Fly app: clawsoul-router                        │
        │   shared-cpu-1x · 256 MB · ~$2/month total        │
        │                                                   │
        │   - FastAPI /telegram/<bot-token> webhook receiver│
        │   - APScheduler (one timer for every user's cron) │
        │   - Fly Machines API client (wake / suspend)      │
        │   - Supabase reader (user_settings, user_machines)│
        └────────────────┬──────────────────────────────────┘
                         │  HTTP /dispatch
                         │  + Fly wake API
                         ▼
        ┌───────────────────────────────────────────────────┐
        │   Per-user worker machines                         │
        │   Fly app: clawsoul-worker (multiple machines)    │
        │   Each: shared-cpu-1x · 256 MB · auto-suspend     │
        │                                                   │
        │   - One machine pinned per active user            │
        │   - Bound by CLAW_USER_ID env                     │
        │   - Listens at /dispatch for chat / cron events   │
        │   - Talks back to Telegram via outbound HTTPS     │
        │   - Suspends after 5 min idle                     │
        └───────────────────────────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────────┐
        │   Shared state                                     │
        │   - Supabase Postgres: user_settings, sessions,    │
        │     memories, user_machines                        │
        │   - Object storage (Fly Tigris or R2):             │
        │     photos, generated files                        │
        └───────────────────────────────────────────────────┘
```

### Why this shape

- **Router is cheap & always-on** because it has the cron clock + webhook
  receiver. Both need always-on.
- **Worker machines are dedicated** so one user's runaway code can't
  starve another. Auto-suspend keeps idle cost ~$0.20/user/month.
- **State is centralised** because each worker machine is *ephemeral* —
  it must wake fresh and re-read state from Postgres + object store,
  not from a local volume.

---

## Per-tier behaviour

| Tier | Worker machine | Proactive | Selfies | Cost/user/month |
|------|----------------|-----------|---------|-----------------|
| **Free / Trial** | Auto-suspend, webhook-only, reactive replies only | ❌ | ❌ | ~$0.20 (storage) |
| **Paid** ($5-10/mo) | Always-on OR woken by scheduler for cron ticks | ✅ | ✅ | ~$2 + LLM (covered by sub) |
| **Enterprise** | Dedicated always-on, custom resources | ✅ | ✅ | bespoke |

Free → Paid upgrade: scheduler starts firing this user's cron jobs;
no code change in the worker beyond reading the user's tier from Postgres.

---

## Telegram: webhook over polling (for the new path)

Today every user has a long-polling PTB Application inside the shared
process. The container-per-user model **cannot** afford long-polling
because the machine would never go to sleep.

We switch to **webhook**:

1. When the user saves their bot token via the dashboard, the router
   calls Telegram `setWebhook` with URL
   `https://clawsoul-router.fly.dev/telegram/<bot-token>`.
2. Telegram POSTs every incoming message to that URL.
3. Router looks up `bot_token → user_id → machine_id`.
4. Router calls Fly Machines API `POST /apps/clawsoul-worker/machines/<machine_id>/start`
   if the machine is suspended.
5. Router POSTs the Telegram update to the worker's `/dispatch` endpoint.
6. Worker processes, sends reply via outbound `https://api.telegram.org/.../sendMessage`,
   stays awake another 30s for follow-up, then suspends.

Polling code stays available behind a tier flag so single-tenant /
self-hosted users (and the current production deploy) keep working.

---

## Database additions

### `public.user_machines` (new table)

```sql
create table user_machines (
    user_id      uuid primary key references auth.users(id) on delete cascade,
    machine_id   text not null,           -- Fly machine id (e.g. 9080d707a647e8)
    region       text not null,
    state        text not null default 'pending',  -- pending | running | suspended | stopped | destroyed
    webhook_url  text,                    -- last set webhook URL
    tier         text not null default 'free',     -- free | paid | enterprise
    created_at   timestamptz default now(),
    updated_at   timestamptz default now(),
    last_active  timestamptz default now()
);
```

### Shifts from per-tenant SQLite to shared Postgres

Currently each user has their own `claw_soul.db` (events + turns + FTS5).
For Phase 2 this needs to live in shared Postgres so worker machines
can be ephemeral. Schema migration plan TBD in Phase 2g.

---

## Rollout phases

### Phase 2a — Router/Scheduler service (week 1)
- New Fly app `clawsoul-router`, new code at `claw_soul/router/`
- FastAPI with `/telegram/<token>` + `/health` + `/admin/users`
- Reads Supabase, calls Fly API
- **Production unaffected** — old single-process keeps serving.

### Phase 2b — Worker mode (week 1)
- `CLAW_USER_ID` env → entrypoint runs in worker mode
- Worker mode = FastAPI listening for `/dispatch` from the router
- No internal scheduler, no PTB polling
- Same image, different launch behaviour

### Phase 2c — Webhook setup (week 2)
- Router exposes `setWebhook` flow
- Dashboard "Save token" → router sets webhook on the user's bot
- For new users, switch from polling to webhook automatically

### Phase 2d — Per-user machine provisioning (week 2)
- `claw_soul/fly_client.py` — Fly Machines API wrapper
- On user upgrade to Paid → spawn worker machine via API
- Save machine_id in `user_machines`
- On cancellation → destroy machine

### Phase 2e — Existing user migration (a few hours)
- Migrate Eric's data from the shared process's `/data/users/a9c257c8-.../`
  to a fresh worker machine
- Verify selfie + proactive + chat all keep working

### Phase 2f — Free tier auto-suspend (week 3)
- Worker auto-suspends after 5 min idle
- Router wakes on webhook / cron tick

### Phase 2g — Move sessions.db & memories to Postgres (later)
- Worker becomes truly stateless (only reads from Postgres + object store)
- Volume becomes optional (only for caches)

---

## Open decisions to confirm before implementing

1. **One worker app or one machine per app**?
   Fly supports both: `clawsoul-worker` app with N machines, or `clawsoul-worker-<uid>` apps.
   Going with **one app, N machines** — simpler, single image tag, easy to redeploy all.

2. **Webhook URL routing**: shared router at `/telegram/<token>` (chosen)
   vs `fly-replay` to specific machine (deferred to Phase 2g if needed).

3. **Scheduler clock**: APScheduler on the router using shared Postgres
   job store (so router restarts don't lose jobs).

4. **Migration of Eric's account**: cut over in one step, or run in
   parallel? Recommendation: parallel — keep existing process running
   for him, new users go through new path, migrate him last.

5. **Pricing**: hold for Phase 2d/e — we don't charge until per-user
   machines are real and we can attribute cost.

---

## What "doing Phase 2 now" actually means

- Old `claw_soul` Fly app stays as-is, keeps Eric on it
- New `clawsoul-router` Fly app + new `clawsoul-worker` Fly app
- New code lives under `claw_soul/router/` and `claw_soul/worker.py`
- Most existing `claw_soul/` code is shared (agent, llm, memory) —
  worker mode just re-uses it without the PTB polling layer.

This commit puts the **foundation** in place: schema, Fly client
skeleton, router stub, design doc. The first wave (2a-b) follows.
