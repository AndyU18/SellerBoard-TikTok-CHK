-- 0012: devoluciones/cancelaciones (RMA) y envíos (logística).
--  * return_case: un caso por devolución o cancelación de la plataforma (TikTok: returns y cancellations).
--  * return_case_line: los productos del caso (una línea = una unidad).
--  * shipment: tiempos de cada orden (pago, listo para enviar, recolección, entrega, fecha límite).

create table sales.return_case (
  id                bigint generated always as identity primary key,
  account_id        bigint not null references sales.account (id) on delete cascade,
  external_id       text not null,
  kind              text not null check (kind in ('return', 'cancellation')),
  case_type         text,            -- RETURN_AND_REFUND | REFUND | REPLACEMENT | EXCHANGE | CANCEL | BUYER_CANCEL
  status            text,
  reason            text,            -- texto legible
  reason_code       text,
  initiated_by      text,            -- buyer | seller | system | operator
  external_order_id text not null,
  case_date         date not null,   -- fecha UTC de creación
  created_at        timestamptz,
  updated_at        timestamptz,
  refund_total      numeric(14, 4) not null default 0,
  imported_at       timestamptz not null default now(),
  unique (account_id, external_id)
);
create index return_case_date_idx on sales.return_case (case_date, account_id);
create index return_case_order_idx on sales.return_case (external_order_id);

create table sales.return_case_line (
  id           bigint generated always as identity primary key,
  case_id      bigint not null references sales.return_case (id) on delete cascade,
  sku          text not null,
  product_name text,
  units        integer not null default 1,
  refund_total numeric(14, 4) not null default 0
);
create index return_case_line_case_idx on sales.return_case_line (case_id);

create table sales.shipment (
  id                bigint generated always as identity primary key,
  account_id        bigint not null references sales.account (id) on delete cascade,
  external_order_id text not null,
  order_date        date not null,
  status            text,
  shipping_type     text,            -- SELLER | PLATFORM
  fulfillment_type  text,
  delivery_option   text,
  carrier           text,
  tracking_number   text,
  is_sample         boolean not null default false,
  ordered_at        timestamptz,
  rts_at            timestamptz,     -- listo para enviar
  collected_at      timestamptz,
  delivered_at      timestamptz,
  due_at            timestamptz,     -- fecha límite de envío
  imported_at       timestamptz not null default now(),
  unique (account_id, external_order_id)
);
create index shipment_date_idx on sales.shipment (order_date, account_id);

alter table sales.return_case      enable row level security;
alter table sales.return_case_line enable row level security;
alter table sales.shipment         enable row level security;
create policy read_sales_return_case on sales.return_case
  for select to authenticated using ((select private.has_permission('sales.view')));
create policy read_sales_return_case_line on sales.return_case_line
  for select to authenticated using ((select private.has_permission('sales.view')));
create policy read_sales_shipment on sales.shipment
  for select to authenticated using ((select private.has_permission('sales.view')));
grant select on sales.return_case, sales.return_case_line, sales.shipment to authenticated;
