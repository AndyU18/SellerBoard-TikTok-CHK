-- 0007: capa oro. El dashboard lee SOLO de aquí.
-- La fórmula de utilidad vive en un único lugar (backend/app/profit.py); el job de refresco la
-- aplica y reescribe solo los días afectados. net_profit es una columna generada para que la
-- tabla nunca quede inconsistente consigo misma.

create table gold.daily_profit (
  id                 bigint generated always as identity primary key,
  profit_date        date not null,
  channel_code       text not null references sales.channel (code),
  account_id         bigint not null references sales.account (id) on delete cascade,
  product_id         bigint references catalog.product (id) on delete cascade,  -- null = publicidad sin SKU
  orders             integer not null default 0,
  units              integer not null default 0,
  gross_sales        numeric(14, 4) not null default 0,
  promo              numeric(14, 4) not null default 0,
  platform_fees      numeric(14, 4) not null default 0,
  shipping_cost      numeric(14, 4) not null default 0,
  refund_amount      numeric(14, 4) not null default 0,
  ad_spend           numeric(14, 4) not null default 0,
  cogs               numeric(14, 4) not null default 0,
  allocated_expenses numeric(14, 4) not null default 0,
  net_profit         numeric(14, 4) generated always as (
    gross_sales - promo - platform_fees - shipping_cost - refund_amount
    - ad_spend - cogs - allocated_expenses
  ) stored,
  refreshed_at       timestamptz not null default now()
);
create unique index daily_profit_grain_uq
  on gold.daily_profit (profit_date, account_id, coalesce(product_id, 0));
create index daily_profit_channel_date_idx on gold.daily_profit (channel_code, profit_date);
create index daily_profit_product_date_idx on gold.daily_profit (product_id, profit_date);
create index daily_profit_account_idx on gold.daily_profit (account_id);

create table gold.refresh_log (
  id           bigint generated always as identity primary key,
  refreshed_at timestamptz not null default now(),
  date_from    date not null,
  date_to      date not null,
  rows_written integer not null default 0,
  reason       text,                           -- sync | cogs_change | expense_change | manual
  sync_run_id  bigint references ops.sync_run (id) on delete set null
);
create index refresh_log_sync_run_idx on gold.refresh_log (sync_run_id);

-- Vistas derivadas: utilidad por unidad y margen sin dividir entre cero.
create view gold.monthly_profit with (security_invoker = true) as
select
  date_trunc('month', profit_date)::date as month,
  channel_code,
  account_id,
  product_id,
  sum(units)              as units,
  sum(gross_sales)        as gross_sales,
  sum(net_profit)         as net_profit,
  sum(net_profit) / nullif(sum(units), 0)       as unit_profit,
  sum(net_profit) / nullif(sum(gross_sales), 0) as margin
from gold.daily_profit
group by 1, 2, 3, 4;
