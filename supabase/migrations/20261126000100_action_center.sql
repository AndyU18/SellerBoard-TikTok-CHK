-- 0020: Centro de acciones.
-- Las acciones se CALCULAN en cada consulta a partir de los datos (no se guardan). Aquí solo queda el
-- estado que le da el usuario a cada una: hecha, descartada o pospuesta. La clave (item_key) es estable
-- (por ejemplo "carrier_dimensions:XMAS-9072"), así el estado sobrevive entre cálculos.

create table ops.action_status (
  item_key         text primary key,
  status           text not null check (status in ('done', 'dismissed', 'snoozed')),
  note             text,
  snoozed_until    date,
  updated_by       uuid,
  updated_by_email text,
  updated_at       timestamp with time zone not null default now()
);
create index action_status_status_idx on ops.action_status (status);

alter table ops.action_status enable row level security;
create policy read_ops_action_status on ops.action_status
  for select to authenticated using ((select private.has_permission('profit.view')));
grant select on ops.action_status to authenticated;
