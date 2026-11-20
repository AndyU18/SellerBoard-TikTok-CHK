-- 0006: gastos indirectos con prorrateo, inventario y órdenes de compra.

-- ---------------------------------------------------------------- gastos
create table finance.expense_category (
  id        bigint generated always as identity primary key,
  code      text not null unique,
  name      text not null,
  is_active boolean not null default true
);
insert into finance.expense_category (code, name) values
  ('software',      'Software y herramientas'),
  ('payroll',       'Nómina'),
  ('storage',       'Almacenamiento y bodega'),
  ('photo_content', 'Fotografía y contenido'),
  ('accounting',    'Contabilidad y legales'),
  ('other',         'Otros');

create table finance.allocation_rule (
  id        bigint generated always as identity primary key,
  name      text not null unique,
  method    text not null check (method in ('revenue', 'units', 'orders', 'equal', 'manual')),
  scope     jsonb not null default '{}',       -- p. ej. {"channels":["amazon"]}
  is_active boolean not null default true
);
insert into finance.allocation_rule (name, method) values ('Por ventas (todos los canales)', 'revenue');

-- Gasto puntual o recurrente. El job de prorrateo lo reparte en expense_allocation.
create table finance.expense (
  id                 bigint generated always as identity primary key,
  category_id        bigint not null references finance.expense_category (id),
  description        text not null,
  amount             numeric(14, 4) not null check (amount >= 0),
  currency           char(3) not null default 'USD',
  recurrence         text not null default 'one_time' check (recurrence in ('one_time', 'monthly', 'annual')),
  start_date         date not null,
  end_date           date check (end_date is null or end_date >= start_date),
  channel_code       text references sales.channel (code),
  account_id         bigint references sales.account (id) on delete set null,
  allocation_rule_id bigint references finance.allocation_rule (id),
  is_active          boolean not null default true,
  created_by         uuid,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index expense_category_id_idx on finance.expense (category_id);
create index expense_channel_code_idx on finance.expense (channel_code);
create index expense_account_id_idx on finance.expense (account_id);
create index expense_rule_id_idx on finance.expense (allocation_rule_id);
create index expense_start_date_idx on finance.expense (start_date);
create trigger expense_touch before update on finance.expense
  for each row execute function private.touch_updated_at();

create table finance.expense_allocation (
  id              bigint generated always as identity primary key,
  expense_id      bigint not null references finance.expense (id) on delete cascade,
  allocation_date date not null,
  account_id      bigint references sales.account (id) on delete cascade,
  product_id      bigint references catalog.product (id) on delete cascade,
  amount          numeric(14, 4) not null check (amount >= 0)
);
create index expense_allocation_expense_idx on finance.expense_allocation (expense_id, allocation_date);
create index expense_allocation_date_idx on finance.expense_allocation (allocation_date, account_id);
create index expense_allocation_account_idx on finance.expense_allocation (account_id);
create index expense_allocation_product_idx on finance.expense_allocation (product_id);

-- ---------------------------------------------------------------- inventario
create table ops.inventory_location (
  id         bigint generated always as identity primary key,
  name       text not null unique,
  kind       text not null check (kind in ('fba', 'awd', 'tiktok_fulfillment', 'wfs', 'warehouse', '3pl')),
  account_id bigint references sales.account (id) on delete set null,
  is_active  boolean not null default true
);
create index inventory_location_account_idx on ops.inventory_location (account_id);

create table ops.inventory_snapshot (
  snapshot_date date not null,
  location_id   bigint not null references ops.inventory_location (id) on delete cascade,
  product_id    bigint not null references catalog.product (id) on delete cascade,
  qty_on_hand   integer not null default 0 check (qty_on_hand >= 0),
  qty_inbound   integer not null default 0 check (qty_inbound >= 0),
  qty_reserved  integer not null default 0 check (qty_reserved >= 0),
  primary key (snapshot_date, location_id, product_id)
);
create index inventory_snapshot_product_idx on ops.inventory_snapshot (product_id, snapshot_date desc);
create index inventory_snapshot_location_idx on ops.inventory_snapshot (location_id);

-- ---------------------------------------------------------------- compras
create table ops.purchase_order (
  id            bigint generated always as identity primary key,
  supplier_id   bigint not null references catalog.supplier (id),
  po_number     text not null unique,
  status        text not null default 'draft'
                check (status in ('draft', 'ordered', 'in_transit', 'received', 'cancelled')),
  ordered_on    date,
  expected_on   date,
  received_on   date,
  currency      char(3) not null default 'USD',
  shipping_cost numeric(14, 4) not null default 0 check (shipping_cost >= 0),
  duties        numeric(14, 4) not null default 0 check (duties >= 0),
  other_costs   numeric(14, 4) not null default 0 check (other_costs >= 0),
  notes         text,
  created_by    uuid,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index purchase_order_supplier_idx on ops.purchase_order (supplier_id);
create index purchase_order_status_idx on ops.purchase_order (status, expected_on);
create trigger purchase_order_touch before update on ops.purchase_order
  for each row execute function private.touch_updated_at();

create table ops.purchase_order_line (
  id           bigint generated always as identity primary key,
  po_id        bigint not null references ops.purchase_order (id) on delete cascade,
  product_id   bigint not null references catalog.product (id),
  qty_ordered  integer not null check (qty_ordered > 0),
  qty_received integer not null default 0 check (qty_received >= 0),
  unit_cost    numeric(14, 4) not null check (unit_cost >= 0),
  unique (po_id, product_id)
);
create index purchase_order_line_product_idx on ops.purchase_order_line (product_id);
