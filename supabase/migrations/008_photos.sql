-- 008_photos.sql
--
-- Cross-machine photo gallery index.  Photos are generated on the
-- per-user worker machine (writes JPEG to local /data) and uploaded
-- to Tigris under `users/<uid>/<filename>`; the worker then inserts
-- one row here so the legacy web dashboard (running on a separate
-- Fly machine with its own /data volume) can list / display them in
-- the Memory Gallery without needing access to the worker's volume.
--
-- Bytes live in Tigris; this table is just metadata + the object key.

create table if not exists public.photos (
    id         bigint generated always as identity primary key,
    user_id    uuid not null references auth.users(id) on delete cascade,
    filename   text not null,           -- basename of the local JPEG
    object_key text not null,           -- Tigris key: users/<uid>/<filename>
    kind       text,                    -- selfie | candid_animal | candid_food | ...
    caption    text,
    ts         timestamptz not null default now(),
    unique (user_id, filename)
);

create index if not exists idx_photos_user_ts
    on public.photos(user_id, ts desc);

-- Row-level security: a user only sees their own rows.  The worker
-- writes via the service-role key (bypasses RLS) and the web app
-- reads via the service-role key after resolving tenancy from the
-- auth cookie — so the gallery endpoints can return the user's set
-- without needing a JWT-authed PostgREST call.
alter table public.photos enable row level security;

drop policy if exists "photos: user reads own" on public.photos;
create policy "photos: user reads own"
    on public.photos for select
    using (auth.uid() = user_id);
