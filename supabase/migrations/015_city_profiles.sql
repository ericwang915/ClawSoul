-- 015_city_profiles.sql
--
-- Per-country city profiles: coordinates (for weather), IANA timezone,
-- and a curated "vibe" blurb used to ground the companion's hometown in
-- her backstory.
--
-- Same three-tier design as culture_calendars (migration 009):
--   1. in-process memo
--   2. this Pg table (fast per-machine cache)
--   3. Tigris object culture/cities/<CC>.json (cross-machine source)
--
-- One row per country; the payload holds all that country's cities so a
-- single lookup hydrates every city we offer for it.  Seeded by
-- scripts/seed_city_profiles.py — NOT generated at runtime.

create table if not exists public.city_profiles (
    country_code text primary key,            -- ISO-3166-1 alpha-2, e.g. "US"
    payload      jsonb not null,
    generated_at timestamptz not null default now(),
    source       text not null                -- manual | tigris-restored
);

-- Global, non-sensitive lookup served by the worker/web via the
-- service-role key; no RLS.
