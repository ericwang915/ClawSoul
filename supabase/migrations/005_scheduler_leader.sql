-- 005_scheduler_leader.sql
--
-- Per-tick leader election for the router/scheduler service.
--
-- The router runs N>=1 machines for HA on the HTTP side, but the
-- APScheduler inside each machine would otherwise fire every cron job
-- N times (duplicate proactive messages, duplicate planner writes,
-- duplicate selfies).  We serialize per (job_id, fire_minute) via a
-- tiny claim table: each machine tries to INSERT before dispatching;
-- the unique PK guarantees exactly one winner.
--
-- The table is append-only — a nightly cron (or pg_cron) prunes rows
-- older than 24 h.  Kept dead-simple so any reader can audit the
-- "who fired what when" by SELECTing it directly.

create table if not exists public.scheduled_runs (
    job_id      text not null,
    fire_minute timestamptz not null,    -- caller truncates to minute
    machine_id  text,                    -- helpful for debugging
    claimed_at  timestamptz not null default now(),
    primary key (job_id, fire_minute)
);

create index if not exists idx_scheduled_runs_claimed_at
    on public.scheduled_runs(claimed_at desc);

-- Operator-only table; service role bypasses RLS, so we don't enable it.
-- (No end-user has any reason to read this.)
