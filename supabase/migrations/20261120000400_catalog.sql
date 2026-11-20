-- 0004: catálogo maestro, listings por canal e historial de costos con vigencia.

create table catalog.brand (
  id   bigint generated always as identity primary key,
  name text not null unique
);

create table catalog.category (
  id        bigint generated always as identity primary key,
  parent_id bigint references catalog.category (id) on delete set null,
  name      text not null,
  unique (parent_id, name)
);
create index category_parent_id_idx on catalog.category (parent_id);

create table catalog.supplier (
  id             bigint generated always as identity primary key,
  name           text not null unique,
  contact        jsonb not null default '{}',
  lead_time_days integer check (lead_time_days >= 0),
  currency       char(3) not null default 'USD',
  notes          text,
  is_active      boolean not null default true
);

-- Producto maestro: un SKU unifica los listings de todos los canales.
create table catalog.product (
  id          bigint generated always as identity primary key,
  sku         text not null unique,
  name        text,
  brand_id    bigint references catalog.brand (id) on delete set null,
  category_id bigint references catalog.category (id) on delete set null,
  supplier_id bigint references catalog.supplier (id) on delete set null,
  gtin        text,
  status      text not null default 'active' check (status in ('active', 'inactive', 'discontinued')),
  weight_oz   numeric(10, 2) check (weight_oz >= 0),
  length_in   numeric(10, 2) check (length_in >= 0),
  width_in    numeric(10, 2) check (width_in >= 0),
  height_in   numeric(10, 2) check (height_in >= 0),
  notes       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index product_brand_id_idx on catalog.product (brand_id);
create index product_category_id_idx on catalog.product (category_id);
create index product_supplier_id_idx on catalog.product (supplier_id);
create trigger product_touch before update on catalog.product
  for each row execute function private.touch_updated_at();

-- Combos/kits: un producto compuesto por otros.
create table catalog.product_bundle_item (
  bundle_product_id    bigint not null references catalog.product (id) on delete cascade,
  component_product_id bigint not null references catalog.product (id) on delete restrict,
  quantity             integer not null check (quantity > 0),
  primary key (bundle_product_id, component_product_id),
  check (bundle_product_id <> component_product_id)
);
create index bundle_item_component_idx on catalog.product_bundle_item (component_product_id);

-- Cómo se llama el producto en cada cuenta/canal.
create table catalog.channel_listing (
  id                  bigint generated always as identity primary key,
  product_id          bigint not null references catalog.product (id) on delete cascade,
  account_id          bigint not null references sales.account (id) on delete cascade,
  channel_sku         text not null,
  external_listing_id text,                    -- ASIN, item id, product id de TikTok
  title               text,
  status              text not null default 'active',
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (account_id, channel_sku)
);
create index channel_listing_product_id_idx on catalog.channel_listing (product_id);
create trigger channel_listing_touch before update on catalog.channel_listing
  for each row execute function private.touch_updated_at();

create table catalog.listing_price_history (
  id         bigint generated always as identity primary key,
  listing_id bigint not null references catalog.channel_listing (id) on delete cascade,
  price      numeric(14, 4) not null check (price >= 0),
  currency   char(3) not null default 'USD',
  valid_from date not null,
  unique (listing_id, valid_from)
);

-- ---------------------------------------------------------------- costos
-- Costo unitario = suma de componentes (producto, flete, aranceles...), cada uno con vigencia.
-- Cambiar un costo agrega una fila nueva; la utilidad pasada no se reescribe.
create table catalog.cost_component_type (
  id        bigint generated always as identity primary key,
  code      text not null unique,
  name      text not null,
  is_active boolean not null default true
);
insert into catalog.cost_component_type (code, name) values
  ('product_cost',    'Costo del producto'),
  ('inbound_freight', 'Flete de entrada'),
  ('duties',          'Aranceles e impuestos de importación'),
  ('prep_labeling',   'Preparación y etiquetado'),
  ('packaging',       'Empaque');

create table catalog.product_cost_history (
  id                bigint generated always as identity primary key,
  product_id        bigint not null references catalog.product (id) on delete cascade,
  component_type_id bigint not null references catalog.cost_component_type (id),
  amount            numeric(14, 4) not null check (amount >= 0),
  currency          char(3) not null default 'USD',
  valid_from        date not null,
  note              text,
  created_by        uuid,
  created_at        timestamptz not null default now(),
  unique (product_id, component_type_id, valid_from)
);
create index product_cost_history_component_idx on catalog.product_cost_history (component_type_id);

-- COGS unitario de un producto en una fecha: última vigencia de cada componente <= fecha.
-- (Supone costos en la moneda base; la conversión se hace en el backend con sales.fx_rate.)
create or replace function catalog.unit_cogs_on(p_product_id bigint, p_day date)
returns numeric
language sql
stable
set search_path = ''
as $$
  select coalesce(sum(c.amount), 0)
  from (
    select distinct on (component_type_id) amount
    from catalog.product_cost_history
    where product_id = p_product_id and valid_from <= p_day
    order by component_type_id, valid_from desc
  ) c;
$$;

-- COGS vigente hoy por producto. security_invoker: respeta el RLS de quien consulta.
create view catalog.product_cost_current with (security_invoker = true) as
select p.id as product_id, p.sku, catalog.unit_cogs_on(p.id, current_date) as cogs_unit
from catalog.product p;
