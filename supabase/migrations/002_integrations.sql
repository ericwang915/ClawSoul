-- Per-user integration credentials store.
--
-- Holds API keys (Tavily, OpenAI, ElevenLabs, …) and OAuth tokens
-- (Google, Twitter, Reddit, …) for each user as a single JSONB blob:
--
--   {
--     "tavily":  { "api_key": "tvly-..." },
--     "openai":  { "api_key": "sk-..." },
--     "google":  { "access_token": "...", "refresh_token": "...", "expires_at": 1779... },
--     "twitter": { "access_token": "...", "scopes": ["read"] },
--     ...
--   }
--
-- Daemon reads with the service_role key. Frontend reads/writes via Supabase
-- JS using the user's JWT — RLS policy already in 001 limits to own row.
--
-- Run in: https://supabase.com/dashboard/project/<ref>/sql/new

alter table public.user_settings
    add column if not exists integrations jsonb not null default '{}'::jsonb;

-- Quick lookup by integration name (used by the daemon to list "users who
-- have configured Tavily" etc., should we ever need that).
create index if not exists user_settings_integrations_gin
    on public.user_settings using gin (integrations jsonb_path_ops);
