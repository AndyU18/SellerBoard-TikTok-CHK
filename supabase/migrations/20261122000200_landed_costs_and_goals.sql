-- 0016: recepciones de contenedores (costo puesto en bodega con aranceles y flete) y metas mensuales.
--  * inbound_receipt: una fila por contenedor + PO + SKU, tal como la informa el reporte de landed cost.
--    fob_unit = precio de fábrica; duty_unit y freight_unit = aranceles y flete por unidad.
--    Alimenta el simulador de aranceles y el historial de costos (COGS).
--  * goal: meta mensual de ventas, utilidad o unidades, global o por canal.

create table ops.inbound_receipt (
  id            bigint generated always as identity primary key,
  source        text not null default 'landed_cost_report',
  container     text not null,
  received_date date not null,
  po            text not null default '',
  sku           text not null,
  description   text,
  qty           integer not null check (qty > 0),
  fob_unit      numeric(14, 4) not null check (fob_unit >= 0),
  duty_unit     numeric(14, 6) not null default 0 check (duty_unit >= 0),
  freight_unit  numeric(14, 6) not null default 0 check (freight_unit >= 0),
  imported_at   timestamp with time zone not null default now(),
  unique (container, po, sku)
);
create index inbound_receipt_sku_idx on ops.inbound_receipt (sku, received_date desc);
create index inbound_receipt_date_idx on ops.inbound_receipt (received_date);

create table finance.goal (
  id           bigint generated always as identity primary key,
  month        date not null check (extract(day from month) = 1),
  metric       text not null check (metric in ('gross_sales', 'net_profit', 'units')),
  channel_code text references sales.channel (code),
  target       numeric(14, 2) not null check (target >= 0),
  created_at   timestamp with time zone not null default now(),
  updated_at   timestamp with time zone not null default now()
);
create unique index goal_unique_idx on finance.goal (month, metric, coalesce(channel_code, ''));
create index goal_month_idx on finance.goal (month);

alter table ops.inbound_receipt enable row level security;
alter table finance.goal        enable row level security;
create policy read_ops_inbound_receipt on ops.inbound_receipt
  for select to authenticated using ((select private.has_permission('inventory.view')));
create policy read_finance_goal on finance.goal
  for select to authenticated using ((select private.has_permission('profit.view')));
grant select on ops.inbound_receipt, finance.goal to authenticated;
