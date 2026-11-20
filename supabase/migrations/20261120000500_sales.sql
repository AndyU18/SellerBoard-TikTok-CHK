-- 0005: capa plata de ventas: órdenes, líneas, reembolsos, eventos financieros, liquidaciones,
-- impuestos, publicidad y tipos de cambio.
-- Convención: costos SIEMPRE positivos en order_line/ad_spend_daily. financial_event.amount es
-- el libro de la plataforma y va con signo (+ ingreso para el vendedor, - cargo).

create table sales.sales_order (
  id                bigint generated always as identity primary key,
  account_id        bigint not null references sales.account (id) on delete cascade,
  external_order_id text not null,
  ordered_at        timestamptz,
  order_date        date not null,                 -- fecha UTC de creación
  status            text not null,
  is_settled        boolean not null default false,
  settled_at        timestamptz,
  currency          char(3) not null default 'USD',
  ship_state        text,                          -- para impuestos por estado
  raw_payload_id    bigint references bronze.raw_payload (id) on delete set null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (account_id, external_order_id)
);
create index sales_order_date_idx on sales.sales_order (order_date, account_id);
create index sales_order_raw_idx on sales.sales_order (raw_payload_id);
create trigger sales_order_touch before update on sales.sales_order
  for each row execute function private.touch_updated_at();

-- order_date y account_id se copian de la orden a propósito: los reportes filtran por fecha y
-- cuenta sin hacer join. El backend los mantiene sincronizados.
create table sales.order_line (
  id             bigint generated always as identity primary key,
  order_id       bigint not null references sales.sales_order (id) on delete cascade,
  account_id     bigint not null references sales.account (id) on delete cascade,
  order_date     date not null,
  product_id     bigint not null references catalog.product (id),
  channel_sku    text not null,
  units          integer not null check (units >= 0),
  gross_sales    numeric(14, 4) not null default 0,
  promo          numeric(14, 4) not null default 0 check (promo >= 0),
  platform_fees  numeric(14, 4) not null default 0 check (platform_fees >= 0),
  shipping_cost  numeric(14, 4) not null default 0 check (shipping_cost >= 0),
  refund_amount  numeric(14, 4) not null default 0 check (refund_amount >= 0),
  tax_collected  numeric(14, 4) not null default 0 check (tax_collected >= 0),
  unique (order_id, product_id)
);
create index order_line_date_account_idx on sales.order_line (order_date, account_id);
create index order_line_product_date_idx on sales.order_line (product_id, order_date);
create index order_line_account_id_idx on sales.order_line (account_id);

-- Detalle de reembolsos (order_line.refund_amount guarda el total agregado).
create table sales.refund (
  id            bigint generated always as identity primary key,
  order_id      bigint not null references sales.sales_order (id) on delete cascade,
  order_line_id bigint references sales.order_line (id) on delete set null,
  account_id    bigint not null references sales.account (id) on delete cascade,
  refund_date   date not null,
  amount        numeric(14, 4) not null check (amount >= 0),
  units         integer not null default 0 check (units >= 0),
  reason        text,
  external_id   text,
  unique (account_id, external_id)
);
create index refund_order_id_idx on sales.refund (order_id);
create index refund_order_line_id_idx on sales.refund (order_line_id);
create index refund_date_idx on sales.refund (refund_date, account_id);

-- Eventos del libro de la plataforma (comisiones, ajustes, reservas, pagos...).
create table sales.financial_event (
  id             bigint generated always as identity primary key,
  account_id     bigint not null references sales.account (id) on delete cascade,
  order_id       bigint references sales.sales_order (id) on delete set null,
  event_type     text not null
                 check (event_type in ('fee', 'shipping', 'refund', 'adjustment', 'reserve', 'payout', 'tax', 'other')),
  event_date     date not null,
  amount         numeric(14, 4) not null,        -- con signo
  currency       char(3) not null default 'USD',
  description    text,
  external_id    text,
  raw_payload_id bigint references bronze.raw_payload (id) on delete set null
);
create unique index financial_event_external_uq
  on sales.financial_event (account_id, external_id) where external_id is not null;
create index financial_event_order_idx on sales.financial_event (order_id);
create index financial_event_date_idx on sales.financial_event (event_date, account_id);
create index financial_event_raw_idx on sales.financial_event (raw_payload_id);

create table sales.settlement (
  id           bigint generated always as identity primary key,
  account_id   bigint not null references sales.account (id) on delete cascade,
  external_id  text not null,
  period_start date,
  period_end   date,
  payout_date  date,
  currency     char(3) not null default 'USD',
  gross_amount numeric(14, 4) not null default 0,
  fees_amount  numeric(14, 4) not null default 0,
  net_payout   numeric(14, 4) not null default 0,
  unique (account_id, external_id)
);
create index settlement_payout_idx on sales.settlement (payout_date);

-- Impuestos por orden y jurisdicción (sales tax).
create table sales.sales_tax_line (
  id                       bigint generated always as identity primary key,
  order_id                 bigint not null references sales.sales_order (id) on delete cascade,
  jurisdiction             text not null,          -- estado / condado
  tax_type                 text not null default 'sales_tax',
  amount                   numeric(14, 4) not null,
  collected_by_marketplace boolean not null default true,
  unique (order_id, jurisdiction, tax_type)
);

-- Publicidad
create table sales.ad_campaign (
  id          bigint generated always as identity primary key,
  account_id  bigint not null references sales.account (id) on delete cascade,
  external_id text not null,
  name        text not null,
  ad_type     text,                                -- sponsored_products | gmv_max | ...
  status      text,
  created_at  timestamptz not null default now(),
  unique (account_id, external_id)
);

create table sales.ad_spend_daily (
  id               bigint generated always as identity primary key,
  account_id       bigint not null references sales.account (id) on delete cascade,
  campaign_id      bigint references sales.ad_campaign (id) on delete set null,
  product_id       bigint references catalog.product (id) on delete set null,
  spend_date       date not null,
  spend            numeric(14, 4) not null check (spend >= 0),
  impressions      bigint not null default 0 check (impressions >= 0),
  clicks           bigint not null default 0 check (clicks >= 0),
  attributed_sales numeric(14, 4) not null default 0,
  attributed_units integer not null default 0
);
create unique index ad_spend_daily_grain_uq
  on sales.ad_spend_daily (account_id, spend_date, coalesce(campaign_id, 0), coalesce(product_id, 0));
create index ad_spend_daily_date_idx on sales.ad_spend_daily (spend_date, account_id);
create index ad_spend_daily_campaign_idx on sales.ad_spend_daily (campaign_id);
create index ad_spend_daily_product_idx on sales.ad_spend_daily (product_id);

-- Multimoneda
create table sales.fx_rate (
  rate_date     date not null,
  from_currency char(3) not null,
  to_currency   char(3) not null,
  rate          numeric(18, 8) not null check (rate > 0),
  source        text,
  primary key (rate_date, from_currency, to_currency)
);
