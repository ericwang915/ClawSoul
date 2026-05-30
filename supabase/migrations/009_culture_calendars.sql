-- 009_culture_calendars.sql
--
-- Per-country cultural calendar cache.  The agent / planner / proactive
-- code asks ``culture.get_calendar("US")`` and gets a JSON blob with
-- fixed-date holidays (Christmas, Valentine's), movable holidays
-- (Thanksgiving 2026 = Nov 26), shopping events, and observances — so
-- the proactive scheduler can drop "happy Thanksgiving!" and the
-- planner can route the day around the right cultural rhythm.
--
-- Three-tier lookup:
--   1. This Pg table         — fast per-machine cache
--   2. Tigris (s3://culture) — cross-machine source of truth
--   3. Claude generation     — last resort, then persisted up the chain
--
-- Calendars are global / not per-user (so no RLS) — the prefix already
-- isolates by country code, and the data is not sensitive.

create table if not exists public.culture_calendars (
    country_code text primary key,            -- ISO-3166-1 alpha-2, e.g. "US"
    payload      jsonb not null,
    generated_at timestamptz not null default now(),
    source       text not null                -- claude-opus-4-7 | tigris-restored | manual
);

-- No RLS: global lookup, served by the worker via the service-role key
-- and not exposed to end-users directly.
