-- 007_user_companion.sql
--
-- The companion-personality wizard's source of truth.
--
-- Web dashboard (legacy `clawsoul` app) and the per-user worker
-- (`clawsoul-worker` machines) run in different containers with
-- different filesystems — so writing the wizard's choices to a local
-- JSON / Markdown file like the single-tenant code path used to do
-- means the worker never sees what the user picked in the dashboard.
--
-- Move it to Postgres.  One row per user, one big JSON blob with the
-- whole choices payload (userName, companionName, archetype, traits,
-- backstory, …).  Worker reads on boot and materializes SOUL.md /
-- PERSONA.md / PROFILE.md locally so the existing Agent code path
-- doesn't need to change.

create table if not exists public.user_companion (
    user_id     uuid primary key references auth.users(id) on delete cascade,
    choices     jsonb not null,                    -- cleaned wizard output
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists idx_user_companion_updated
    on public.user_companion(updated_at desc);

alter table public.user_companion enable row level security;

drop policy if exists "user owns row user_companion" on public.user_companion;
create policy "user owns row user_companion"
    on public.user_companion for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- touch updated_at on update (re-uses helper from migration 004)
drop trigger if exists user_companion_updated_at on public.user_companion;
create trigger user_companion_updated_at  before update on public.user_companion
    for each row execute function public.touch_updated_at();
