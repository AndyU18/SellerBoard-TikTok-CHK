-- 0021: TikTok Shop, datos extra (solo lectura de la API de TikTok Shop).
--  * catálogo: tiktok_shop_product (con la URL de la imagen principal, nunca el archivo) y
--    tiktok_shop_product_sku (une el id de SKU de TikTok con el seller_sku del vendedor).
--  * rendimiento: tiktok_shop_product_performance / tiktok_shop_sku_performance, una foto por día con la
--    ventana que cubre (historial acotado a 90 días por el sincronizador).
--  * promociones: tiktok_shop_promotion + tiktok_shop_promotion_product; cupones: tiktok_shop_coupon.
--  * operación: tiktok_shop_package (estados y tiempos, SIN datos del comprador), tiktok_shop_warehouse
--    (sin líneas de calle ni contacto) y tiktok_shop_webhook.
--  * dinero: tiktok_shop_payment (solo los últimos 4 dígitos de la cuenta bancaria; statement_ids enlaza el
--    pago con sales.settlement.external_id) y tiktok_shop_withdrawal.
-- Todas las claves son ids de TikTok (texto): cada sincronización es un upsert idempotente.

create table ops.tiktok_shop_product (
  product_id            text primary key,
  title                 text,
  status                text,
  audit_status          text,
  audit_reasons         jsonb,
  listing_quality_tier  text,
  is_not_for_sale       boolean not null default false,
  main_image_url        text,
  thumb_url             text,
  image_count           integer not null default 0,
  brand_name            text,
  categories            jsonb,
  sales_regions         jsonb,
  min_price             numeric(14, 4),
  max_price             numeric(14, 4),
  currency              text,
  sku_count             integer not null default 0,
  created_at            timestamp with time zone,
  updated_at            timestamp with time zone,
  synced_at             timestamp with time zone not null default now()
);
create index tiktok_shop_product_status_idx on ops.tiktok_shop_product (status);

create table ops.tiktok_shop_product_sku (
  sku_id       text primary key,
  product_id   text not null,
  seller_sku   text,
  price        numeric(14, 4),
  currency     text,
  quantity     integer not null default 0
);
create index tiktok_shop_product_sku_product_idx on ops.tiktok_shop_product_sku (product_id);
create index tiktok_shop_product_sku_seller_idx on ops.tiktok_shop_product_sku (seller_sku);

create table ops.tiktok_shop_product_performance (
  product_id          text not null,
  snapshot_date       date not null,
  window_start        date not null,
  window_end          date not null,
  gmv                 numeric(14, 4) not null default 0,
  orders              integer not null default 0,
  units_sold          integer not null default 0,
  impressions         bigint,
  page_views          bigint,
  page_visitors       bigint,
  click_through_rate  numeric(9, 6),
  conversion_rate     numeric(9, 6),
  gmv_video           numeric(14, 4),
  gmv_live            numeric(14, 4),
  gmv_product_card    numeric(14, 4),
  primary key (product_id, snapshot_date)
);
create index tiktok_shop_product_performance_day_idx on ops.tiktok_shop_product_performance (snapshot_date desc);

create table ops.tiktok_shop_sku_performance (
  sku_id         text not null,
  snapshot_date  date not null,
  product_id     text,
  window_start   date not null,
  window_end     date not null,
  gmv            numeric(14, 4) not null default 0,
  orders         integer not null default 0,
  units_sold     integer not null default 0,
  primary key (sku_id, snapshot_date)
);
create index tiktok_shop_sku_performance_day_idx on ops.tiktok_shop_sku_performance (snapshot_date desc);

create table ops.tiktok_shop_promotion (
  activity_id    text primary key,
  title          text,
  activity_type  text,
  product_level  text,
  status         text,
  duration_type  text,
  begin_at       timestamp with time zone,
  end_at         timestamp with time zone,
  created_at     timestamp with time zone,
  updated_at     timestamp with time zone,
  product_count  integer not null default 0,
  discount       jsonb,
  synced_at      timestamp with time zone not null default now()
);
create index tiktok_shop_promotion_status_idx on ops.tiktok_shop_promotion (status);

create table ops.tiktok_shop_promotion_product (
  activity_id        text not null,
  product_id         text not null,
  activity_price     numeric(14, 4),
  currency           text,
  discount           jsonb,
  quantity_limit     integer,
  quantity_per_user  integer,
  primary key (activity_id, product_id)
);
create index tiktok_shop_promotion_product_product_idx on ops.tiktok_shop_promotion_product (product_id);

create table ops.tiktok_shop_coupon (
  coupon_id         text primary key,
  title             text,
  status            text,
  display_type      text,
  promo_code        text,
  product_scope     text,
  target_segment    text,
  discount_type     text,
  discount_percent  numeric(9, 4),
  discount_amount   numeric(14, 4),
  max_discount      numeric(14, 4),
  threshold_type    text,
  min_spend         numeric(14, 4),
  currency          text,
  claim_start       timestamp with time zone,
  claim_end         timestamp with time zone,
  redeem_start      timestamp with time zone,
  redeem_end        timestamp with time zone,
  redemption_limit  integer,
  per_buyer_limit   integer,
  claimed_count     integer,
  redeemed_count    integer,
  product_ids       jsonb,
  creation_source   text,
  created_at        timestamp with time zone,
  updated_at        timestamp with time zone,
  synced_at         timestamp with time zone not null default now()
);
create index tiktok_shop_coupon_status_idx on ops.tiktok_shop_coupon (status);

create table ops.tiktok_shop_package (
  package_id       text primary key,
  status           text,
  tracking_number  text,
  order_ids        jsonb,
  order_count      integer not null default 0,
  unit_count       integer not null default 0,
  created_at       timestamp with time zone,
  updated_at       timestamp with time zone,
  delivered_at     timestamp with time zone,   -- inferido: updated_at de un paquete COMPLETED
  last_event_at    timestamp with time zone,   -- último evento de seguimiento (solo paquetes no terminados)
  last_event       text,
  synced_at        timestamp with time zone not null default now()
);
create index tiktok_shop_package_status_idx on ops.tiktok_shop_package (status);
create index tiktok_shop_package_created_idx on ops.tiktok_shop_package (created_at desc);

create table ops.tiktok_shop_payment (
  payment_id              text primary key,
  status                  text,
  currency                text,
  amount                  numeric(14, 4) not null default 0,
  settlement_amount       numeric(14, 4) not null default 0,
  reserve_amount          numeric(14, 4) not null default 0,
  amount_before_exchange  numeric(14, 4),
  exchange_rate           numeric(14, 6),
  created_at              timestamp with time zone,
  paid_at                 timestamp with time zone,
  bank_last4              text,                -- NUNCA el número completo
  statement_ids           jsonb
);
create index tiktok_shop_payment_status_idx on ops.tiktok_shop_payment (status);
create index tiktok_shop_payment_created_idx on ops.tiktok_shop_payment (created_at desc);

create table ops.tiktok_shop_withdrawal (
  withdrawal_id  text primary key,
  type           text,
  status         text,
  currency       text,
  amount         numeric(14, 4) not null default 0,
  created_at     timestamp with time zone
);
create index tiktok_shop_withdrawal_status_idx on ops.tiktok_shop_withdrawal (status);
create index tiktok_shop_withdrawal_created_idx on ops.tiktok_shop_withdrawal (created_at desc);

create table ops.tiktok_shop_warehouse (
  warehouse_id  text primary key,
  name          text,
  type          text,
  sub_type      text,
  status        text,
  is_default    boolean not null default false,
  city          text,
  state         text,
  region_code   text,
  postal_code   text
);

create table ops.tiktok_shop_webhook (
  event_type  text not null,
  address     text not null,
  created_at  timestamp with time zone,
  primary key (event_type, address)
);

alter table ops.tiktok_shop_product              enable row level security;
alter table ops.tiktok_shop_product_sku          enable row level security;
alter table ops.tiktok_shop_product_performance  enable row level security;
alter table ops.tiktok_shop_sku_performance      enable row level security;
alter table ops.tiktok_shop_promotion            enable row level security;
alter table ops.tiktok_shop_promotion_product    enable row level security;
alter table ops.tiktok_shop_coupon               enable row level security;
alter table ops.tiktok_shop_package              enable row level security;
alter table ops.tiktok_shop_payment              enable row level security;
alter table ops.tiktok_shop_withdrawal           enable row level security;
alter table ops.tiktok_shop_warehouse            enable row level security;
alter table ops.tiktok_shop_webhook              enable row level security;

create policy read_ops_tiktok_shop_product on ops.tiktok_shop_product
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_product_sku on ops.tiktok_shop_product_sku
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_product_performance on ops.tiktok_shop_product_performance
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_sku_performance on ops.tiktok_shop_sku_performance
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_promotion on ops.tiktok_shop_promotion
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_promotion_product on ops.tiktok_shop_promotion_product
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_coupon on ops.tiktok_shop_coupon
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_package on ops.tiktok_shop_package
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_payment on ops.tiktok_shop_payment
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_withdrawal on ops.tiktok_shop_withdrawal
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_warehouse on ops.tiktok_shop_warehouse
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_shop_webhook on ops.tiktok_shop_webhook
  for select to authenticated using ((select private.has_permission('profit.view')));

grant select on
  ops.tiktok_shop_product, ops.tiktok_shop_product_sku, ops.tiktok_shop_product_performance,
  ops.tiktok_shop_sku_performance, ops.tiktok_shop_promotion, ops.tiktok_shop_promotion_product,
  ops.tiktok_shop_coupon, ops.tiktok_shop_package, ops.tiktok_shop_payment, ops.tiktok_shop_withdrawal,
  ops.tiktok_shop_warehouse, ops.tiktok_shop_webhook
to authenticated;
