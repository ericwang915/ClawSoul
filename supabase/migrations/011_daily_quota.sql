-- 011_daily_quota.sql
--
-- Durable per-user daily message quota.
--
-- The quota counter used to live only in the worker process's memory.
-- Workers idle-suspend aggressively (free tier ~3 min), and the counter
-- reset to 0 on every cold boot — so the "N messages/UTC-day" cap that
-- protects the operator's shared LLM API key was effectively unbounded
-- for anyone chatting in bursts.
--
-- This table persists the count across cold boots; the RPC increments
-- it atomically (read-modify-write in app code would race across the
-- worker's concurrent turns).

create table if not exists public.daily_quota (
    user_id  uuid not null references auth.users(id) on delete cascade,
    day      date not null,             -- UTC calendar day
    messages integer not null default 0,
    primary key (user_id, day)
);

create index if not exists idx_daily_quota_day on public.daily_quota(day);

-- Atomic increment-and-return.  One round trip, no read-modify-write race.
create or replace function public.increment_daily_messages(p_user_id uuid, p_day date)
returns integer
language plpgsql
as $$
declare
    new_count integer;
begin
    insert into public.daily_quota (user_id, day, messages)
    values (p_user_id, p_day, 1)
    on conflict (user_id, day)
    do update set messages = public.daily_quota.messages + 1
    returning messages into new_count;
    return new_count;
end;
$$;

-- Service-role-only table (worker / web); no RLS needed.
