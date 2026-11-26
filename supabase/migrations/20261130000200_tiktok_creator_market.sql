-- Mercado de creadores y colaboraciones del VENDEDOR de TikTok Shop (SOLO LECTURA de la API Affiliate Seller).
--  * tiktok_market_creator: creadores públicos del mercado (usuario, seguidores, categorías, GMV/unidades
--    reportados). `creator_open_id` es el id opaco de TikTok; la API no da el id numérico. `active` pasa a false
--    cuando un creador no se ve en una carga completa durante mucho tiempo (la búsqueda no es un listado exhaustivo).
--    Las columnas de la ficha (engagement, GPM, ...) quedan nulas hasta que se enriquece al creador (`detail_at`).
--  * tiktok_market_creator_snapshot: foto semanal (lunes) de seguidores y GMV; el sincronizador la acota a 26 semanas.
--  * tiktok_open_collaboration: colaboraciones abiertas propias (producto y comisión ofrecida).
--  * tiktok_target_collaboration: colaboraciones dirigidas propias (invitaciones). Sin el texto del mensaje.
-- Solo datos públicos de creadores. Claves = ids de TikTok (texto): cada sincronización es un upsert idempotente.

create table ops.tiktok_market_creator (
  creator_open_id           text primary key,
  username                  text,
  nickname                  text,
  avatar_url                text,
  region                    text,
  follower_count            bigint not null default 0,
  creator_level             integer,
  category_ids              jsonb,
  gmv_amount                numeric(14, 4),
  video_gmv_amount          numeric(14, 4),
  gmv_range                 text,
  gmv_range_min             numeric(14, 4),
  units_sold_range          text,
  units_sold_min            bigint,
  avg_video_views           bigint,
  avg_live_viewers          bigint,
  demographics              jsonb,
  currency                  text,
  engagement_rate           numeric(9, 6),
  live_engagement_rate      numeric(9, 6),
  gpm_amount                numeric(14, 4),
  avg_gmv_per_buyer         numeric(14, 4),
  avg_commission_rate       numeric(9, 6),
  rating                    numeric(6, 3),
  promoted_product_num      integer,
  brand_collaboration_count integer,
  units_sold_total          bigint,
  detail_at                 timestamp with time zone,
  first_seen_at             timestamp with time zone not null default now(),
  last_seen_at              timestamp with time zone not null default now(),
  active                    boolean not null default true
);
create index tiktok_market_creator_username_idx on ops.tiktok_market_creator (lower(username));
create index tiktok_market_creator_followers_idx on ops.tiktok_market_creator (follower_count desc);
create index tiktok_market_creator_first_seen_idx on ops.tiktok_market_creator (first_seen_at desc);
create index tiktok_market_creator_active_idx on ops.tiktok_market_creator (active);

create table ops.tiktok_market_creator_snapshot (
  creator_open_id  text not null,
  week_start       date not null,
  follower_count   bigint not null default 0,
  gmv_amount       numeric(14, 4),
  gmv_range_min    numeric(14, 4),
  primary key (creator_open_id, week_start)
);
create index tiktok_market_creator_snapshot_week_idx on ops.tiktok_market_creator_snapshot (week_start desc);

create table ops.tiktok_open_collaboration (
  collaboration_id        text primary key,
  product_id              text,
  product_title           text,
  image_url               text,
  currency                text,
  price_min               numeric(14, 4),
  price_max               numeric(14, 4),
  inventory               bigint,
  product_status          text,
  status                  text,
  commission_rate         numeric(9, 6),
  commission_start        timestamp with time zone,
  commission_end          timestamp with time zone,
  creator_count           integer,
  showcase_creator_count  integer,
  first_seen_at           timestamp with time zone not null default now(),
  last_seen_at            timestamp with time zone not null default now(),
  active                  boolean not null default true
);
create index tiktok_open_collaboration_product_idx on ops.tiktok_open_collaboration (product_id);

create table ops.tiktok_target_collaboration (
  collaboration_id        text primary key,
  name                    text,
  kind                    text,
  status                  text,
  product_count           integer,
  creator_count           integer,
  invited_count           integer,
  showcase_creator_count  integer,
  start_time              timestamp with time zone,
  end_time                timestamp with time zone,
  created_time            timestamp with time zone,
  updated_time            timestamp with time zone,
  has_free_sample         boolean not null default false,
  sample_approval_exempt  boolean not null default false,
  first_seen_at           timestamp with time zone not null default now(),
  last_seen_at            timestamp with time zone not null default now(),
  active                  boolean not null default true
);

alter table ops.tiktok_market_creator           enable row level security;
alter table ops.tiktok_market_creator_snapshot  enable row level security;
alter table ops.tiktok_open_collaboration       enable row level security;
alter table ops.tiktok_target_collaboration     enable row level security;

create policy read_ops_tiktok_market_creator on ops.tiktok_market_creator
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_market_creator_snapshot on ops.tiktok_market_creator_snapshot
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_open_collaboration on ops.tiktok_open_collaboration
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_target_collaboration on ops.tiktok_target_collaboration
  for select to authenticated using ((select private.has_permission('profit.view')));

grant select on
  ops.tiktok_market_creator, ops.tiktok_market_creator_snapshot,
  ops.tiktok_open_collaboration, ops.tiktok_target_collaboration
to authenticated;
