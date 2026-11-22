-- 0015: desempeño del vendedor (Walmart) e inventario/estado de los listados.
--  * seller_performance: foto diaria de los indicadores de Walmart por ventana (7/14/30/60/90 días):
--    ots = enviado a tiempo, otd = entregado a tiempo, vtr = guía de rastreo válida, tasas de reembolso
--    y de cancelación (por culpa del vendedor o del cliente). Todos en porcentaje.
--  * listing_stock: estado actual de cada SKU publicado (precio, publicación, existencias).

create table sales.seller_performance (
  id                          bigint generated always as identity primary key,
  account_id                  bigint not null references sales.account (id) on delete cascade,
  snapshot_date               date not null,
  window_days                 integer not null,
  ots                         numeric(8, 4),
  otd                         numeric(8, 4),
  vtr                         numeric(8, 4),
  refund_rate_seller_fault    numeric(8, 4),
  refund_rate_customer_fault  numeric(8, 4),
  cancellation_seller_fault   numeric(8, 4),
  cancellation_customer_fault numeric(8, 4),
  imported_at                 timestamp with time zone not null default now(),
  unique (account_id, snapshot_date, window_days)
);
create index seller_performance_date_idx on sales.seller_performance (snapshot_date desc, account_id);

create table sales.listing_stock (
  id                  bigint generated always as identity primary key,
  account_id          bigint not null references sales.account (id) on delete cascade,
  sku                 text not null,
  product_name        text,
  price               numeric(14, 4),
  availability        text,
  published_status    text,
  lifecycle_status    text,
  unpublished_reasons text,
  avail_to_sell       integer,
  reserved            integer,
  input_qty           integer,
  imported_at         timestamp with time zone not null default now(),
  unique (account_id, sku)
);

alter table sales.seller_performance enable row level security;
alter table sales.listing_stock      enable row level security;
create policy read_sales_seller_performance on sales.seller_performance
  for select to authenticated using ((select private.has_permission('sales.view')));
create policy read_sales_listing_stock on sales.listing_stock
  for select to authenticated using ((select private.has_permission('sales.view')));
grant select on sales.seller_performance, sales.listing_stock to authenticated;
