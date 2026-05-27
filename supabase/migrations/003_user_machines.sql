-- 003_user_machines.sql
--
-- Per-user Fly Machine bookkeeping for Phase 2 (container-per-user SaaS).
-- One row per active user; the router/scheduler reads this to know which
-- Fly machine to wake when an event lands for that user.
--
-- States:
--   pending     — provisioning API call in flight
--   running     — machine is awake and accepting /dispatch
--   suspended   — Fly auto-suspended; needs an API wake call to serve
--   stopped     — manually stopped (e.g. user paused subscription)
--   destroyed   — terminated (e.g. user cancelled); kept for audit, no machine
-- Tiers:
--   free        — webhook-only, reactive replies, no proactive/selfies
--   paid        — full proactive + scheduled selfies + RAG
--   enterprise  — always-on dedicated, custom resources

create table if not exists public.user_machines (
    user_id      uuid primary key references auth.users(id) on delete cascade,
    machine_id   text not null,
    region       text not null,
    state        text not null default 'pending',
    tier         text not null default 'free',
    webhook_url  text,
    image_ref    text,                          -- Fly image tag this machine was launched with
    cpu_kind     text default 'shared',
    cpus         int  default 1,
    memory_mb    int  default 256,
    created_at   timestamptz default now(),
    updated_at   timestamptz default now(),
    last_active  timestamptz default now(),
    last_wake_ms int                             -- last wake-up latency, for SLO tracking
);

create index if not exists idx_user_machines_state on public.user_machines(state);
create index if not exists idx_user_machines_tier  on public.user_machines(tier);

-- Touch updated_at on every row change.
create or replace function public.user_machines_set_updated_at()
returns trigger as $$
begin
    new.updated_at := now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists user_machines_updated_at on public.user_machines;
create trigger user_machines_updated_at
    before update on public.user_machines
    for each row execute function public.user_machines_set_updated_at();

-- Row-level security: a user can only see their own row.
-- The router/scheduler uses the service-role key and bypasses RLS.
alter table public.user_machines enable row level security;

drop policy if exists "users access own machine row" on public.user_machines;
create policy "users access own machine row"
    on public.user_machines
    for all
    using ( auth.uid() = user_id )
    with check ( auth.uid() = user_id );
