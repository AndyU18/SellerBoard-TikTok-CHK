-- 0011: datos para Promociones/subsidios, Dayparting y Flujo de caja.
--  * order_line.platform_discount: descuento que paga la plataforma (subsidio). NO resta utilidad:
--    el vendedor no lo paga. order_line.promo sigue siendo el descuento del vendedor.
--  * order_line.list_amount: venta a precio de lista (precio original x unidades), para medir la
--    profundidad del descuento. 0 = no se conoce (CSV, Walmart).
--  * sales_order.ordered_at ya existía: ahora los conectores lo llenan (hora exacta, UTC).
--  * settlement: estado del pago, fecha de cierre del estado de cuenta y componentes.

alter table sales.order_line
  add column platform_discount numeric(14, 4) not null default 0 check (platform_discount >= 0),
  add column list_amount       numeric(14, 4) not null default 0 check (list_amount >= 0);

alter table sales.settlement
  add column status            text,                     -- paid | processing | failed
  add column statement_date    date,                     -- cierre del estado de cuenta
  add column shipping_amount   numeric(14, 4) not null default 0,
  add column adjustment_amount numeric(14, 4) not null default 0;
create index settlement_statement_idx on sales.settlement (statement_date);
