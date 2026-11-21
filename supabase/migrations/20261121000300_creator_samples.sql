-- 0013: muestras a creadores (ROI de gifting).
--  * sample_application: solicitudes de muestra de creadores (embudo pendiente/aprobada/...). Las
--    aprobadas traen el id de la orden de muestra.
--  * sample_line: productos enviados en cada orden de muestra (para costear la muestra aunque la
--    orden nunca se liquide: vale $0 y TikTok no genera transacción).
--  * affiliate_order: ventas atribuidas a cada creador (comisión, video, estado de liquidación).
-- La comisión de afiliado ya va dentro de platform_fees de la utilidad; aquí solo sirve para atribuir.

create table sales.sample_application (
  id                bigint generated always as identity primary key,
  account_id        bigint not null references sales.account (id) on delete cascade,
  external_id       text not null,
  creator_username  text,
  follower_count    integer,
  creator_gmv       numeric(14, 4),
  status            text,
  external_order_id text,                 -- orden de muestra (solo si fue aprobada y enviada)
  product_title     text,
  commission_rate   numeric(8, 4),
  imported_at       timestamp with time zone not null default now(),
  unique (account_id, external_id)
);
create index sample_application_order_idx on sales.sample_application (external_order_id);
create index sample_application_creator_idx on sales.sample_application (creator_username);

create table sales.sample_line (
  id                bigint generated always as identity primary key,
  account_id        bigint not null references sales.account (id) on delete cascade,
  external_order_id text not null,
  order_date        date not null,
  sku               text not null,
  product_name      text,
  units             integer not null default 1,
  unique (account_id, external_order_id, sku)
);
create index sample_line_date_idx on sales.sample_line (order_date, account_id);

create table sales.affiliate_order (
  id                bigint generated always as identity primary key,
  account_id        bigint not null references sales.account (id) on delete cascade,
  external_order_id text not null,
  sku_id            text not null,
  order_date        date not null,
  creator_username  text,
  content_type      text,
  content_id        text,
  sku               text,
  units             integer not null default 1,
  commission_base   numeric(14, 4) not null default 0,
  commission        numeric(14, 4) not null default 0,   -- real si ya se liquidó, si no estimada
  commission_rate   numeric(8, 4),
  settlement_status text,
  imported_at       timestamp with time zone not null default now(),
  unique (account_id, external_order_id, sku_id)
);
create index affiliate_order_date_idx on sales.affiliate_order (order_date, account_id);
create index affiliate_order_creator_idx on sales.affiliate_order (creator_username);

alter table sales.sample_application enable row level security;
alter table sales.sample_line        enable row level security;
alter table sales.affiliate_order    enable row level security;
create policy read_sales_sample_application on sales.sample_application
  for select to authenticated using ((select private.has_permission('sales.view')));
create policy read_sales_sample_line on sales.sample_line
  for select to authenticated using ((select private.has_permission('sales.view')));
create policy read_sales_affiliate_order on sales.affiliate_order
  for select to authenticated using ((select private.has_permission('sales.view')));
grant select on sales.sample_application, sales.sample_line, sales.affiliate_order to authenticated;
