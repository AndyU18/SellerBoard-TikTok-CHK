-- 0023: TikTok Creator (la cuenta de creador propia; SOLO LECTURA de la API Affiliate Creator).
--  * tiktok_creator_profile: foto diaria del perfil y permisos (sin tokens).
--  * tiktok_creator_product: catálogo de afiliados (colaboración abierta). `active` pasa a false cuando un
--    producto deja de aparecer tras una carga completa; `suspect` marca importes/unidades imposibles que trae
--    la API (datos de prueba) y cuyo dinero no se guarda. La API no expone el id de la tienda: solo su nombre.
--  * tiktok_creator_product_history: foto semanal (precio, comisión, unidades); el sincronizador la acota a 26 semanas.
--  * tiktok_creator_showcase_product: vitrina propia del creador.
--  * tiktok_creator_order: pedidos de afiliado (estructura sin verificar: se conserva el crudo en `payload`).
-- Claves = ids de TikTok (texto): cada sincronización es un upsert idempotente.

create table ops.tiktok_creator_profile (
  snapshot_date     date primary key,
  creator_user_id   text,
  username          text,
  avatar_url        text,
  user_type         text,
  seller_type       text,
  register_region   text,
  selection_region  text,
  permissions       jsonb,
  catalog_total     integer,
  showcase_total    integer,
  captured_at       timestamp with time zone not null default now()
);

create table ops.tiktok_creator_product (
  product_id          text primary key,
  title               text,
  image_url           text,
  detail_link         text,
  category            text,
  category_leaf       text,
  currency            text,
  original_price_min  numeric(14, 4),
  original_price_max  numeric(14, 4),
  sale_price_min      numeric(14, 4),
  sale_price_max      numeric(14, 4),
  commission_rate     numeric(9, 6),
  commission_amount   numeric(14, 4),
  units_sold          bigint not null default 0,
  has_inventory       boolean not null default false,
  shop_name           text,
  sale_region         text,
  suspect             boolean not null default false,
  first_seen_at       timestamp with time zone not null default now(),
  last_seen_at        timestamp with time zone not null default now(),
  active              boolean not null default true
);
create index tiktok_creator_product_leaf_idx on ops.tiktok_creator_product (category_leaf);
create index tiktok_creator_product_first_seen_idx on ops.tiktok_creator_product (first_seen_at desc);
create index tiktok_creator_product_active_idx on ops.tiktok_creator_product (active);

create table ops.tiktok_creator_product_history (
  product_id         text not null,
  snapshot_date      date not null,
  price              numeric(14, 4),
  commission_rate    numeric(9, 6),
  commission_amount  numeric(14, 4),
  units_sold         bigint not null default 0,
  primary key (product_id, snapshot_date)
);
create index tiktok_creator_product_history_day_idx on ops.tiktok_creator_product_history (snapshot_date desc);

create table ops.tiktok_creator_showcase_product (
  product_id          text not null,
  origin              text not null,
  title               text,
  image_url           text,
  source              text,
  currency            text,
  original_price_min  numeric(14, 4),
  original_price_max  numeric(14, 4),
  discount_price_min  numeric(14, 4),
  discount_price_max  numeric(14, 4),
  added_status        text,
  inventory_status    text,
  review_status       text,
  is_hidden           boolean not null default false,
  shop_name           text,
  last_seen_at        timestamp with time zone not null default now(),
  active              boolean not null default true,
  primary key (product_id, origin)
);

create table ops.tiktok_creator_order (
  order_id           text primary key,
  status             text,
  created_at         timestamp with time zone,
  commission_amount  numeric(14, 4),
  currency           text,
  payload            jsonb,
  synced_at          timestamp with time zone not null default now()
);
create index tiktok_creator_order_created_idx on ops.tiktok_creator_order (created_at desc);

alter table ops.tiktok_creator_profile           enable row level security;
alter table ops.tiktok_creator_product           enable row level security;
alter table ops.tiktok_creator_product_history   enable row level security;
alter table ops.tiktok_creator_showcase_product  enable row level security;
alter table ops.tiktok_creator_order             enable row level security;

create policy read_ops_tiktok_creator_profile on ops.tiktok_creator_profile
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_creator_product on ops.tiktok_creator_product
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_creator_product_history on ops.tiktok_creator_product_history
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_creator_showcase_product on ops.tiktok_creator_showcase_product
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_creator_order on ops.tiktok_creator_order
  for select to authenticated using ((select private.has_permission('profit.view')));

grant select on
  ops.tiktok_creator_profile, ops.tiktok_creator_product, ops.tiktok_creator_product_history,
  ops.tiktok_creator_showcase_product, ops.tiktok_creator_order
to authenticated;
