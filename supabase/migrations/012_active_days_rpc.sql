-- 012_active_days_rpc.sql
--
-- Distinct active-day count, computed in Postgres.
--
-- The milestone emitter (runs after every outbound message) and the
-- dashboard both need "how many distinct UTC days has this user chatted".
-- They used to pull up to 5000 turn rows and de-dup dates in Python on
-- every call — a multi-KB transfer + loop on the hot path.  This RPC does
-- the COUNT(DISTINCT date) server-side and returns a single integer.

create or replace function public.count_active_days(p_user_id uuid, p_since timestamptz)
returns integer
language sql
stable
as $$
    select count(distinct (ts at time zone 'UTC')::date)::int
    from public.turns
    where user_id = p_user_id
      and ts >= p_since;
$$;
