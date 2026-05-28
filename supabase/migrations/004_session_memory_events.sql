-- 004_session_memory_events.sql
--
-- Phase 2g: move per-tenant SQLite state into shared Postgres so worker
-- machines can be stateless (woken from cold and read everything from
-- the central DB).  Schema mirrors the existing SQLite tables but adds
-- a `user_id` column for row-level isolation.
--
-- Migration of existing data happens via claw_soul.migrate.run() (see
-- migrate.py); this file is only the schema.
--
-- Tables:
--   sessions          one row per (user, session_id) — top-level container
--   turns             one row per user/assistant turn (FTS via pg_trgm GIN)
--   memory_entries    long-term memory key/value (with optional category)
--   memory_daily      per-day append-only log (one row per write)
--   events            generic event log for /log_detail (replaces JSONL)

-- ── Extensions ──────────────────────────────────────────────────────

create extension if not exists pg_trgm;     -- trigram GIN for turns FTS

-- ── sessions ────────────────────────────────────────────────────────

create table if not exists public.sessions (
    user_id      uuid not null references auth.users(id) on delete cascade,
    session_id   text not null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    message_count int not null default 0,
    primary key (user_id, session_id)
);
create index if not exists idx_sessions_user_updated
    on public.sessions(user_id, updated_at desc);

-- ── turns (FTS-able verbatim transcript) ────────────────────────────

create table if not exists public.turns (
    id           bigint generated always as identity primary key,
    user_id      uuid not null references auth.users(id) on delete cascade,
    session_id   text not null,
    role         text not null,                 -- user | assistant
    content      text not null,
    ts           timestamptz not null default now(),
    content_hash text not null,
    unique (user_id, content_hash)
);
create index if not exists idx_turns_user_session_ts
    on public.turns(user_id, session_id, ts desc);
-- Trigram index supports cross-session FTS within a user.  Works for both
-- CN (≥3 chars) and EN; falls back to LIKE for shorter CN tokens.
create index if not exists idx_turns_content_trgm
    on public.turns using gin (content gin_trgm_ops);

-- ── memory ──────────────────────────────────────────────────────────

create table if not exists public.memory_entries (
    user_id    uuid not null references auth.users(id) on delete cascade,
    key        text not null,
    content    text not null,
    category   text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (user_id, key)
);
create index if not exists idx_memory_user_updated
    on public.memory_entries(user_id, updated_at desc);

create table if not exists public.memory_daily (
    id         bigint generated always as identity primary key,
    user_id    uuid not null references auth.users(id) on delete cascade,
    day        date not null,
    key        text not null,
    content    text not null,
    ts         timestamptz not null default now()
);
create index if not exists idx_memory_daily_user_day
    on public.memory_daily(user_id, day desc);

-- ── events (replaces history_detail.jsonl) ──────────────────────────

create table if not exists public.events (
    id          bigint generated always as identity primary key,
    user_id     uuid not null references auth.users(id) on delete cascade,
    session_id  text,
    kind        text not null,
    payload     jsonb not null default '{}',
    ts          timestamptz not null default now()
);
create index if not exists idx_events_user_ts
    on public.events(user_id, ts desc);
create index if not exists idx_events_user_kind_ts
    on public.events(user_id, kind, ts desc);

-- ── RLS: users see only their own rows ──────────────────────────────

alter table public.sessions        enable row level security;
alter table public.turns           enable row level security;
alter table public.memory_entries  enable row level security;
alter table public.memory_daily    enable row level security;
alter table public.events          enable row level security;

-- Postgres 15 doesn't have CREATE POLICY IF NOT EXISTS — use the
-- drop-then-create idiom so this migration is re-runnable.

drop policy if exists "user owns row sessions"        on public.sessions;
create policy "user owns row sessions"
    on public.sessions for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "user owns row turns"           on public.turns;
create policy "user owns row turns"
    on public.turns for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "user owns row memory_entries"  on public.memory_entries;
create policy "user owns row memory_entries"
    on public.memory_entries for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "user owns row memory_daily"    on public.memory_daily;
create policy "user owns row memory_daily"
    on public.memory_daily for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "user owns row events"          on public.events;
create policy "user owns row events"
    on public.events for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ── Touch updated_at automatically ──────────────────────────────────

create or replace function public.touch_updated_at()
returns trigger as $$
begin new.updated_at := now(); return new; end;
$$ language plpgsql;

drop trigger if exists sessions_updated_at        on public.sessions;
drop trigger if exists memory_entries_updated_at  on public.memory_entries;

create trigger sessions_updated_at        before update on public.sessions
    for each row execute function public.touch_updated_at();
create trigger memory_entries_updated_at  before update on public.memory_entries
    for each row execute function public.touch_updated_at();
