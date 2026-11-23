-- 0018: Redo completo (solo lectura de la API de facturación).
--  * redo_charge: cada cargo del libro de Redo (etiquetas, correcciones de carrier, anulaciones, comisiones).
--    Antes solo se guardaba el total por orden (sales.shipping_label_cost); aquí queda el detalle.
--  * redo_bill: facturas mensuales con saldo de apertura y cierre.
--  * redo_topup: recargas de saldo (transacciones), que no son costo sino financiamiento.

create table ops.redo_charge (
  id                  text primary key,          -- id del cargo en Redo
  order_name          text,
  subtype             text not null,             -- ChargeLabel | Surcharge | VoidLabel | ClaimCommission | CardProcessingFee ...
  type                text,
  product             text,
  amount              numeric(14, 4) not null,
  currency            text not null default 'USD',
  note                text,
  fulfillment_group_id text,
  shipment_id         text,
  reverses_charge_id  text,
  order_date          timestamp with time zone,
  created_at          timestamp with time zone not null
);
create index redo_charge_created_idx on ops.redo_charge (created_at desc);
create index redo_charge_order_idx on ops.redo_charge (order_name);
create index redo_charge_subtype_idx on ops.redo_charge (subtype);

create table ops.redo_bill (
  id              text primary key,
  type            text,
  status          text,
  period_start    date,
  period_end      date,
  charge_total    numeric(14, 4) not null default 0,
  opening_balance numeric(14, 4) not null default 0,
  closing_balance numeric(14, 4) not null default 0,
  billed_at       timestamp with time zone,
  due_at          timestamp with time zone
);

create table ops.redo_topup (
  id              text primary key,
  amount          numeric(14, 4) not null,
  status          text,
  failure_message text,
  created_at      timestamp with time zone not null
);
create index redo_topup_created_idx on ops.redo_topup (created_at desc);

alter table ops.redo_charge enable row level security;
alter table ops.redo_bill   enable row level security;
alter table ops.redo_topup  enable row level security;
create policy read_ops_redo_charge on ops.redo_charge
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_redo_bill on ops.redo_bill
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_redo_topup on ops.redo_topup
  for select to authenticated using ((select private.has_permission('profit.view')));
grant select on ops.redo_charge, ops.redo_bill, ops.redo_topup to authenticated;
