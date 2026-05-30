-- 010_telegram_updates.sql
--
-- Durable Telegram update_id dedup for the router.
--
-- The router fans inbound webhook updates out to per-user worker
-- machines.  Telegram retries delivery if it doesn't get a fast 200,
-- and a worker that idle-suspended between the first delivery and the
-- retry loses its in-memory dedup ring buffer — so the retried update
-- gets processed twice (double LLM call, double reply, double bill).
--
-- We claim (user_id, update_id) by INSERT at the router BEFORE waking
-- the worker: the unique PK guarantees the second delivery loses the
-- race and is dropped, regardless of worker lifecycle.  Claiming before
-- the wake also avoids paying the cold-start cost for a duplicate.
--
-- Append-only; the router's nightly prune deletes rows older than 24 h
-- (Telegram won't retry an update beyond that window).

create table if not exists public.telegram_updates (
    user_id    uuid not null references auth.users(id) on delete cascade,
    update_id  bigint not null,
    claimed_at timestamptz not null default now(),
    primary key (user_id, update_id)
);

create index if not exists idx_telegram_updates_claimed_at
    on public.telegram_updates(claimed_at);

-- Router-only table; service role bypasses RLS, so we don't enable it.
