-- 0019: desglose por concepto de cada liquidación (statement) de TikTok Shop.
--  Antes solo se guardaba el total de comisiones (sales.settlement.fees_amount); cada transacción
--  de la liquidación trae ventas, descuentos, comisión de referencia/afiliado/pago, envío, impuestos...
--  settlement_fee guarda una fila por (transacción, concepto) con el monto firmado
--  (ingresos positivos, costos negativos, como los entrega TikTok).
--  kind: revenue (ventas/descuentos/reembolsos) | fee (comisiones) | shipping | adjustment | memo
--  (informativo: impuesto de venta y descuento de plataforma no cambian el depósito).

create table sales.settlement_fee (
  id                bigint generated always as identity primary key,
  settlement_id     bigint not null references sales.settlement (id) on delete cascade,
  account_id        bigint not null references sales.account (id) on delete cascade,
  statement_id      text not null,              -- id externo de la liquidación
  transaction_id    text not null,              -- id de la transacción dentro de la liquidación
  external_order_id text,
  concept           text not null,              -- referral_fee | affiliate_commission | transaction_fee ...
  kind              text not null,
  amount            numeric(14, 4) not null,
  fee_date          date,                       -- fecha de creación de la orden
  unique (account_id, statement_id, transaction_id, concept)
);
create index settlement_fee_settlement_idx on sales.settlement_fee (settlement_id);
create index settlement_fee_concept_idx on sales.settlement_fee (concept);
create index settlement_fee_order_idx on sales.settlement_fee (external_order_id);

alter table sales.settlement_fee enable row level security;
create policy read_sales_settlement_fee on sales.settlement_fee
  for select to authenticated using ((select private.has_permission('profit.view')));
grant select on sales.settlement_fee to authenticated;
