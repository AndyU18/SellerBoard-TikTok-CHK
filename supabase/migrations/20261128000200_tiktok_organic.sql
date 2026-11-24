-- 0021: TikTok orgánico (cuenta de contenido propia de CHK; NO TikTok Shop). Solo lectura de la API
-- Display v2 de TikTok. La API entrega solo el valor ACTUAL de cada métrica, así que el sincronizador
-- toma una foto diaria y de ahí sale todo el historial (crecimiento, series).
--  * tiktok_organic_account: una foto por día del perfil y sus contadores.
--  * tiktok_organic_video: datos estables de cada video (la portada es una URL que caduca ~6 h).
--  * tiktok_organic_video_metric: foto diaria de vistas/likes/comentarios/compartidos por video; el
--    sincronizador poda lo que pasa de 365 días.
-- Los tokens OAuth NO viven aquí: están en un archivo local ignorado por git.

create table ops.tiktok_organic_account (
  snapshot_date    date primary key,                 -- una foto por día (idempotente)
  open_id          text,
  username         text,
  display_name     text,
  avatar_url       text,
  bio              text,
  profile_url      text,
  is_verified      boolean,
  follower_count   bigint check (follower_count >= 0),
  following_count  bigint check (following_count >= 0),
  likes_count      bigint check (likes_count >= 0),
  video_count      integer check (video_count >= 0),
  captured_at      timestamp with time zone not null default now()
);

create table ops.tiktok_organic_video (
  video_id          text primary key,
  title             text,
  description       text,
  cover_url         text,
  created_at        timestamp with time zone,
  duration_seconds  integer check (duration_seconds >= 0),
  share_url         text,
  embed_link        text,
  width             integer,
  height            integer,
  updated_at        timestamp with time zone not null default now()
);
create index tiktok_organic_video_created_idx on ops.tiktok_organic_video (created_at desc);

create table ops.tiktok_organic_video_metric (
  video_id       text not null references ops.tiktok_organic_video (video_id) on delete cascade,
  snapshot_date  date not null,
  views          bigint not null default 0 check (views >= 0),
  likes          bigint not null default 0 check (likes >= 0),
  comments       bigint not null default 0 check (comments >= 0),
  shares         bigint not null default 0 check (shares >= 0),
  primary key (video_id, snapshot_date)
);
create index tiktok_organic_video_metric_day_idx on ops.tiktok_organic_video_metric (snapshot_date desc);

alter table ops.tiktok_organic_account       enable row level security;
alter table ops.tiktok_organic_video         enable row level security;
alter table ops.tiktok_organic_video_metric  enable row level security;

create policy read_ops_tiktok_organic_account on ops.tiktok_organic_account
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_organic_video on ops.tiktok_organic_video
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_organic_video_metric on ops.tiktok_organic_video_metric
  for select to authenticated using ((select private.has_permission('profit.view')));

grant select on
  ops.tiktok_organic_account, ops.tiktok_organic_video, ops.tiktok_organic_video_metric
to authenticated;
