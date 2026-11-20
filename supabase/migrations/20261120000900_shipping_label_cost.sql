-- 0009: costo de etiquetas de envío por orden (p. ej. Redo). Vive aparte de order_line para que
-- sobreviva a las re-sincronizaciones de la plataforma y se aplique cuando la orden llegue.
-- gold.daily_profit.shipping_cost = envío de la plataforma + etiqueta repartida entre las líneas
-- de la orden en proporción a sus ventas.

create table sales.shipping_label_cost (
  id                bigint generated always as identity primary key,
  source            text not null,                   -- redo | ...
  external_order_id text not null,                   -- id de la orden en el canal (TikTok, Walmart, #Shopify)
  amount            numeric(14, 4) not null,         -- neto de cargos y reversiones
  charges           integer not null default 1,      -- cuántos cargos se sumaron
  first_charged     date,
  last_charged      date,
  imported_at       timestamptz not null default now(),
  unique (source, external_order_id)
);
create index shipping_label_cost_order_idx on sales.shipping_label_cost (external_order_id);

alter table sales.shipping_label_cost enable row level security;
create policy read_sales_shipping_label_cost on sales.shipping_label_cost
  for select to authenticated using ((select private.has_permission('sales.view')));
grant select on sales.shipping_label_cost to authenticated;
