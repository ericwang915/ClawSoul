-- 013_router_instances.sql
--
-- Liveness registry for router/scheduler sharding.
--
-- The router runs N machines.  Each used to register APScheduler jobs for
-- EVERY active user (≈6 jobs/user) and rely on a per-tick DB claim to keep
-- exactly one from actually dispatching.  At 10k users that's ~60k jobs per
-- machine plus N claim-writes per tick — APScheduler's in-memory jobstore
-- and the wasted claims become the ceiling.
--
-- Sharding fixes this: each instance heartbeats here, derives its index from
-- the sorted list of live instances, and only owns users whose
-- hash(user_id) % N == index.  So each machine holds ~60k/N jobs and only the
-- owner fires a user's tick.  The DB claim (scheduled_runs) stays as a safety
-- net for the brief overlap during scale up/down.
--
-- Rows are heartbeated every reconcile (~60s) and pruned by the router's
-- nightly job once stale.

create table if not exists public.router_instances (
    instance_id text primary key,           -- FLY_MACHINE_ID (or hostname in dev)
    last_seen   timestamptz not null default now()
);

create index if not exists idx_router_instances_last_seen
    on public.router_instances(last_seen);

-- Router-only table; service role bypasses RLS, so we don't enable it.
