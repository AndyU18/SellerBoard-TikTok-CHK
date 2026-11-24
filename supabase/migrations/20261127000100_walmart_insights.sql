-- 0020: apartado Walmart (solo lectura de la API de Walmart Marketplace).
--  * walmart_listing_quality: una foto por día del puntaje de calidad de los listados.
--  * walmart_wfs_inventory: existencias WFS por SKU y nodo (última foto) + walmart_wfs_inventory_history
--    (una foto diaria, acotada a 90 días por el sincronizador).
--  * walmart_feed: cargas (feeds) recientes y su resultado.
--  * walmart_repricer_strategy: estrategias del repricer con sus banderas y reglas (json).
--  * walmart_shipping_template: plantillas de envío con su detalle (json).
--  * walmart_report_request: solicitudes de reporte (ITEM, BUYBOX...) y su estado.
--  * walmart_item_snapshot: foto por SKU tomada del último reporte ITEM (estrategia de repricer,
--    precio mínimo/máximo permitido, Buy Box, tipo de cumplimiento). Es el ÚNICO vínculo SKU -> estrategia
--    que expone la API, porque /v3/repricer/* no lista los SKU asignados.
-- Calidad POR ÍTEM no se guarda: Walmart solo la ofrece por POST (/listingQuality/items), y esta app no
-- escribe ni hace POST a Walmart.

create table ops.walmart_listing_quality (
  snapshot_date           date primary key,        -- una foto por día (idempotente)
  listing_quality         numeric(7, 2),
  offer_score             numeric(7, 2),
  rating_review_score     numeric(7, 2),
  content_score           numeric(7, 2),
  price_score             numeric(7, 2),
  shipping_score          numeric(7, 2),
  transactibility_score   numeric(7, 2),
  item_defect_count       integer,
  defect_ratio            numeric(9, 4),
  captured_at             timestamp with time zone not null default now()
);

create table ops.walmart_wfs_inventory (
  sku                     text not null,
  node                    text not null,           -- nodo de envío; "WFSFulfilled" si Walmart no da id de nodo
  offer_id                text,
  node_type               text,
  avail_to_sell           integer not null default 0,
  on_hand                 integer not null default 0,
  first_in_stock_date     timestamp with time zone,
  modified_at             timestamp with time zone,
  snapshot_at             timestamp with time zone not null default now(),
  primary key (sku, node)
);

create table ops.walmart_wfs_inventory_history (
  sku                     text not null,
  node                    text not null,
  snapshot_date           date not null,
  avail_to_sell           integer not null default 0,
  on_hand                 integer not null default 0,
  primary key (sku, node, snapshot_date)
);
create index walmart_wfs_inventory_history_day_idx on ops.walmart_wfs_inventory_history (snapshot_date desc);

create table ops.walmart_feed (
  feed_id                 text primary key,
  feed_type               text,
  feed_source             text,
  status                  text,
  items_received          integer not null default 0,
  items_succeeded         integer not null default 0,
  items_failed            integer not null default 0,
  items_processing        integer not null default 0,
  data_error_count        integer not null default 0,
  system_error_count      integer not null default 0,
  feed_date               timestamp with time zone,
  modified_at             timestamp with time zone
);
create index walmart_feed_date_idx on ops.walmart_feed (feed_date desc);

create table ops.walmart_repricer_strategy (
  collection_id                       text primary key,
  strategy                            text not null,
  enabled                             boolean not null default false,
  assigned_count                      integer not null default 0,
  enable_for_promotion                boolean,
  restore_seller_price_without_target boolean,
  enable_buybox_meet_external         boolean,
  compare_with_3p_offer_only          boolean,
  strategies                          jsonb not null default '[]'::jsonb,
  snapshot_at                         timestamp with time zone not null default now()
);

create table ops.walmart_shipping_template (
  id                      text primary key,
  name                    text,
  type                    text,
  rate_model_type         text,
  status                  text,
  detail                  jsonb,                   -- null si Walmart no entregó el detalle
  snapshot_at             timestamp with time zone not null default now()
);

create table ops.walmart_report_request (
  request_id              text primary key,
  report_type             text not null,
  status                  text,
  report_version          text,
  submitted_at            timestamp with time zone,
  generated_at            timestamp with time zone
);
create index walmart_report_request_submitted_idx on ops.walmart_report_request (submitted_at desc);

create table ops.walmart_item_snapshot (
  sku                     text primary key,
  item_id                 text,
  product_name            text,
  publish_status          text,
  lifecycle_status        text,
  status_change_reason    text,
  fulfillment_type        text,
  price                   numeric(14, 4),
  buybox_price            numeric(14, 4),
  buybox_eligible         boolean,
  repricer_strategy       text,
  repricer_status         text,
  min_allowed_price       numeric(14, 4),
  max_allowed_price       numeric(14, 4),
  reviews_count           integer,
  average_rating          numeric(5, 2),
  report_request_id       text,
  snapshot_at             timestamp with time zone not null default now()
);

alter table ops.walmart_listing_quality        enable row level security;
alter table ops.walmart_wfs_inventory          enable row level security;
alter table ops.walmart_wfs_inventory_history  enable row level security;
alter table ops.walmart_feed                   enable row level security;
alter table ops.walmart_repricer_strategy      enable row level security;
alter table ops.walmart_shipping_template      enable row level security;
alter table ops.walmart_report_request         enable row level security;
alter table ops.walmart_item_snapshot          enable row level security;

create policy read_ops_walmart_listing_quality on ops.walmart_listing_quality
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_walmart_wfs_inventory on ops.walmart_wfs_inventory
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_walmart_wfs_inventory_history on ops.walmart_wfs_inventory_history
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_walmart_feed on ops.walmart_feed
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_walmart_repricer_strategy on ops.walmart_repricer_strategy
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_walmart_shipping_template on ops.walmart_shipping_template
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_walmart_report_request on ops.walmart_report_request
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_walmart_item_snapshot on ops.walmart_item_snapshot
  for select to authenticated using ((select private.has_permission('profit.view')));

grant select on
  ops.walmart_listing_quality, ops.walmart_wfs_inventory, ops.walmart_wfs_inventory_history,
  ops.walmart_feed, ops.walmart_repricer_strategy, ops.walmart_shipping_template,
  ops.walmart_report_request, ops.walmart_item_snapshot
to authenticated;
