-- 0017: gestor de SKU (alias) y bitácora de notificaciones.
--  * sku_alias: un SKU que llega de una plataforma ("XMAS8027", "XMAS-7702-1PK") y el producto maestro
--    al que pertenece. Al importar, el alias se resuelve al producto maestro; así las variantes no
--    vuelven a crear productos duplicados.
--  * notification_log: cada intento de envío del resumen diario/semanal (o de una prueba), por canal.

create table catalog.sku_alias (
  id         bigint generated always as identity primary key,
  alias      text not null unique,
  product_id bigint not null references catalog.product (id) on delete cascade,
  reason     text,
  created_at timestamp with time zone not null default now()
);
create index sku_alias_product_idx on catalog.sku_alias (product_id);

create table ops.notification_log (
  id         bigint generated always as identity primary key,
  kind       text not null check (kind in ('daily', 'weekly', 'test')),
  channel    text not null check (channel in ('email', 'slack', 'webhook')),
  status     text not null check (status in ('sent', 'failed', 'skipped')),
  detail     text,
  created_at timestamp with time zone not null default now()
);
create index notification_log_created_idx on ops.notification_log (created_at desc);

alter table catalog.sku_alias     enable row level security;
alter table ops.notification_log  enable row level security;
create policy read_catalog_sku_alias on catalog.sku_alias
  for select to authenticated using ((select private.has_permission('catalog.view')));
create policy read_ops_notification_log on ops.notification_log
  for select to authenticated using ((select private.has_permission('settings.view')));
grant select on catalog.sku_alias, ops.notification_log to authenticated;
