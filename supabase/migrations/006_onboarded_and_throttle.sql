-- 006_onboarded_and_throttle.sql
--
-- Two columns on user_machines that together stop the "fresh paid user
-- gets spammed by proactive/selfie/planner before they finish
-- onboarding" failure mode.
--
--   onboarded         boolean — flipped to true by the worker after the
--                     agent's memory has a bot_name set (i.e. the user
--                     completed the name-the-companion step).  The
--                     scheduler refuses to add tick jobs for rows
--                     where this is false, so a newly-provisioned
--                     account stays reactive-only until onboarding is
--                     actually done — regardless of tier.
--
--   last_message_at   timestamptz — updated on every inbound or
--                     outbound Telegram message.  proactive_tick and
--                     selfie_tick skip if now() - last_message_at is
--                     under 30 minutes, so we never stack a check-in
--                     on top of an active conversation.

alter table public.user_machines
    add column if not exists onboarded       boolean     not null default false,
    add column if not exists last_message_at timestamptz not null default now();

create index if not exists idx_user_machines_last_message_at
    on public.user_machines(last_message_at desc);
