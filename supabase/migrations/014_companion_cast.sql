-- 014_companion_cast.sql
--
-- The companion's "cast" — recurring people in her world (mom, dad, a
-- best friend) that she can photograph and talk about consistently.
--
-- Lazily populated: the first time she shares / mentions a person, the
-- agent calls the cast_photo tool with a one-time appearance description;
-- we persist it here (keyed by a normalized slug) so every later photo
-- and every later mention stays the same person — same face (stable seed
-- + a bootstrapped reference image in Tigris), same backstory notes.
--
-- One row per (user, slug).  Generation reads this at tool-call time; the
-- agent also gets the roster injected into memory so chat stays coherent.

create table if not exists public.companion_cast (
    user_id       uuid not null references auth.users(id) on delete cascade,
    slug          text not null,            -- normalized id, e.g. "mom", "lily"
    name          text,                     -- display name, e.g. "林姨"
    relation      text not null,            -- mom | dad | friend | sibling | ...
    appearance    text not null,            -- visual description used for generation
    seed          bigint not null,          -- stable seed → consistent face
    reference_key text,                     -- Tigris object key of the bootstrap reference
    notes         text,                     -- personality/context for chat consistency
    created_at    timestamptz not null default now(),
    primary key (user_id, slug)
);

create index if not exists idx_companion_cast_user
    on public.companion_cast(user_id);

-- RLS: users may read their own cast (the dashboard can surface it); the
-- worker/web write via the service-role key, which bypasses RLS.
alter table public.companion_cast enable row level security;

drop policy if exists "user owns row companion_cast" on public.companion_cast;
create policy "user owns row companion_cast"
    on public.companion_cast for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);
