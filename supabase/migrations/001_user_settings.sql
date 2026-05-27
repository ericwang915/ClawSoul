-- ClawSoul multi-tenant settings table.
--
-- Stores per-user preferences not stored in the auth.users row:
--   * telegram_bot_token  — the user's @BotFather token, used by the daemon
--                           to launch their personal Telegram bot
--   * telegram_chat_id    — the chat ID for proactive messaging
--
-- Run this in the Supabase SQL Editor:
--   https://supabase.com/dashboard/project/<ref>/sql

create table if not exists public.user_settings (
    user_id              uuid primary key references auth.users(id) on delete cascade,
    telegram_bot_token   text,
    telegram_chat_id     bigint,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

-- Keep updated_at fresh on every row change.
create or replace function public.user_settings_set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists user_settings_updated_at on public.user_settings;
create trigger user_settings_updated_at
    before update on public.user_settings
    for each row execute function public.user_settings_set_updated_at();

-- ── Row-Level Security ─────────────────────────────────────────────────────
-- A signed-in user can only see / edit their own row.
-- The daemon reads ALL rows via the service_role key, which bypasses RLS.

alter table public.user_settings enable row level security;

drop policy if exists "users access own row" on public.user_settings;
create policy "users access own row"
    on public.user_settings
    for all
    using  (auth.uid() = user_id)
    with check (auth.uid() = user_id);
