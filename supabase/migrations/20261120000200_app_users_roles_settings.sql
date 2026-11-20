-- 0002: usuarios, roles, permisos granulares, configuración editable, vistas guardadas,
-- alertas, notificaciones y auditoría.

-- ---------------------------------------------------------------- permisos y roles
create table app.permission (
  id          bigint generated always as identity primary key,
  code        text not null unique,
  category    text not null,
  description text not null
);

create table app.role (
  id          bigint generated always as identity primary key,
  code        text not null unique,
  name        text not null,
  description text,
  is_system   boolean not null default false,   -- los roles de sistema no se pueden borrar
  created_at  timestamptz not null default now()
);

create table app.role_permission (
  role_id       bigint not null references app.role (id) on delete cascade,
  permission_id bigint not null references app.permission (id) on delete cascade,
  primary key (role_id, permission_id)
);
create index role_permission_permission_id_idx on app.role_permission (permission_id);

-- ---------------------------------------------------------------- usuarios
create table app.profile (
  user_id      uuid primary key references auth.users (id) on delete cascade,
  email        text,
  full_name    text,
  is_active    boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  last_seen_at timestamptz
);
create trigger profile_touch before update on app.profile
  for each row execute function private.touch_updated_at();

create table app.user_role (
  user_id     uuid   not null references app.profile (user_id) on delete cascade,
  role_id     bigint not null references app.role (id) on delete cascade,
  assigned_by uuid,
  assigned_at timestamptz not null default now(),
  primary key (user_id, role_id)
);
create index user_role_role_id_idx on app.user_role (role_id);

-- ---------------------------------------------------------------- configuración
-- setting_definition declara cada ajuste (tipo, defecto, validación, quién lo edita).
-- setting_value guarda el valor con alcance: global, canal, cuenta o usuario.
create table app.setting_definition (
  key             text primary key,
  category        text not null,
  description     text not null,
  value_type      text not null check (value_type in ('string', 'number', 'boolean', 'json')),
  default_value   jsonb not null,
  validation      jsonb,                       -- p. ej. {"min":0,"max":1} o {"enum":["a","b"]}
  allowed_scopes  text[] not null default array['global'],
  edit_permission text not null default 'settings.edit',
  created_at      timestamptz not null default now()
);

create table app.setting_value (
  id          bigint generated always as identity primary key,
  setting_key text not null references app.setting_definition (key) on delete cascade,
  scope_type  text not null default 'global'
              check (scope_type in ('global', 'channel', 'account', 'user')),
  scope_id    text,
  value       jsonb not null,
  updated_by  uuid,
  updated_at  timestamptz not null default now(),
  check ((scope_type = 'global') = (scope_id is null))
);
create unique index setting_value_scope_uq
  on app.setting_value (setting_key, scope_type, coalesce(scope_id, ''));
create trigger setting_value_touch before update on app.setting_value
  for each row execute function private.touch_updated_at();

-- ---------------------------------------------------------------- vistas, alertas
create table app.saved_view (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references app.profile (user_id) on delete cascade,
  page       text not null,                    -- dashboard | products | pnl ...
  name       text not null,
  filters    jsonb not null default '{}',
  is_shared  boolean not null default false,
  created_at timestamptz not null default now()
);
create index saved_view_user_id_idx on app.saved_view (user_id);

create table app.alert_rule (
  id           bigint generated always as identity primary key,
  code         text not null unique,
  name         text not null,
  rule_type    text not null,                  -- negative_profit | low_margin | missing_cogs | sync_failed ...
  params       jsonb not null default '{}',
  channel_code text,
  severity     text not null default 'warning' check (severity in ('info', 'warning', 'critical')),
  is_active    boolean not null default true,
  created_at   timestamptz not null default now()
);

create table app.notification (
  id            bigint generated always as identity primary key,
  user_id       uuid references app.profile (user_id) on delete cascade, -- null = para todos
  alert_rule_id bigint references app.alert_rule (id) on delete set null,
  title         text not null,
  body          text,
  severity      text not null default 'info' check (severity in ('info', 'warning', 'critical')),
  created_at    timestamptz not null default now(),
  read_at       timestamptz
);
create index notification_user_unread_idx on app.notification (user_id, created_at desc)
  where read_at is null;
create index notification_alert_rule_id_idx on app.notification (alert_rule_id);

-- ---------------------------------------------------------------- auditoría
create table app.audit_log (
  id           bigint generated always as identity primary key,
  occurred_at  timestamptz not null default now(),
  table_schema text not null,
  table_name   text not null,
  row_pk       text,
  action       text not null check (action in ('INSERT', 'UPDATE', 'DELETE')),
  actor_id     uuid,
  old_row      jsonb,
  new_row      jsonb
);
create index audit_log_row_idx on app.audit_log (table_schema, table_name, row_pk, occurred_at desc);
create index audit_log_occurred_idx on app.audit_log (occurred_at desc);

-- ---------------------------------------------------------------- funciones
-- ¿El usuario actual tiene el permiso? Se usa en las políticas RLS.
create or replace function private.has_permission(p_code text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from app.profile pr
    join app.user_role ur on ur.user_id = pr.user_id
    join app.role_permission rp on rp.role_id = ur.role_id
    join app.permission pm on pm.id = rp.permission_id
    where pr.user_id = (select auth.uid())
      and pr.is_active
      and pm.code = p_code
  );
$$;
revoke execute on function private.has_permission(text) from public, anon;
grant execute on function private.has_permission(text) to authenticated;

-- Registra el cambio. El actor es auth.uid() o, si el backend escribe,
-- el valor que fije con: select set_config('app.user_id', '<uuid>', true).
create or replace function private.audit_row()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  actor uuid := coalesce(
    (select auth.uid()),
    nullif(current_setting('app.user_id', true), '')::uuid
  );
begin
  insert into app.audit_log (table_schema, table_name, row_pk, action, actor_id, old_row, new_row)
  values (
    tg_table_schema,
    tg_table_name,
    coalesce(to_jsonb(new) ->> 'id', to_jsonb(old) ->> 'id'),
    tg_op,
    actor,
    case when tg_op <> 'INSERT' then to_jsonb(old) end,
    case when tg_op <> 'DELETE' then to_jsonb(new) end
  );
  return null;
end;
$$;
revoke execute on function private.audit_row() from public, anon, authenticated;

-- Al registrarse un usuario: crea su perfil. El primero de todos queda como Dueño;
-- los demás no tienen permisos hasta que un Dueño/Admin les asigne un rol.
create or replace function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into app.profile (user_id, email, full_name)
  values (new.id, new.email, new.raw_user_meta_data ->> 'full_name');

  if not exists (select 1 from app.user_role) then
    insert into app.user_role (user_id, role_id)
    select new.id, id from app.role where code = 'owner';
  end if;
  return new;
end;
$$;
revoke execute on function private.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function private.handle_new_user();

-- ---------------------------------------------------------------- semillas
insert into app.permission (code, category, description) values
  ('profit.view',    'analytics', 'Ver utilidad y dashboard'),
  ('sales.view',     'analytics', 'Ver órdenes, publicidad y liquidaciones'),
  ('costs.view',     'costs',     'Ver costos (COGS)'),
  ('costs.edit',     'costs',     'Editar costos e historial de costos'),
  ('catalog.view',   'catalog',   'Ver catálogo de productos'),
  ('catalog.edit',   'catalog',   'Editar productos, listings y proveedores'),
  ('expenses.view',  'finance',   'Ver gastos indirectos'),
  ('expenses.edit',  'finance',   'Editar gastos y reglas de prorrateo'),
  ('inventory.view', 'inventory', 'Ver inventario y órdenes de compra'),
  ('inventory.edit', 'inventory', 'Editar inventario y órdenes de compra'),
  ('settings.view',  'settings',  'Ver configuración'),
  ('settings.edit',  'settings',  'Editar configuración'),
  ('import.run',     'data',      'Importar archivos CSV/Excel'),
  ('sync.run',       'data',      'Lanzar sincronizaciones con las plataformas'),
  ('ops.view',       'data',      'Ver sincronizaciones y problemas de calidad de datos'),
  ('users.view',     'admin',     'Ver usuarios'),
  ('users.manage',   'admin',     'Invitar, activar y desactivar usuarios, asignar roles'),
  ('roles.manage',   'admin',     'Crear roles y cambiar sus permisos'),
  ('audit.view',     'admin',     'Ver el registro de auditoría');

insert into app.role (code, name, description, is_system) values
  ('owner',   'Dueño',       'Acceso total, incluida la gestión de roles', true),
  ('admin',   'Admin',       'Todo excepto gestionar roles', true),
  ('analyst', 'Analista',    'Solo lectura de todo el análisis y los costos', true),
  ('ops',     'Operaciones', 'Edita catálogo, costos, inventario y gastos; importa y sincroniza', true);

-- Dueño: todo. Admin: todo menos roles.manage. Analista: todos los *.view.
-- Operaciones: todos los *.view + edición operativa.
insert into app.role_permission (role_id, permission_id)
select r.id, p.id from app.role r cross join app.permission p where r.code = 'owner';

insert into app.role_permission (role_id, permission_id)
select r.id, p.id from app.role r cross join app.permission p
where r.code = 'admin' and p.code <> 'roles.manage';

insert into app.role_permission (role_id, permission_id)
select r.id, p.id from app.role r cross join app.permission p
where r.code = 'analyst' and p.code like '%.view';

insert into app.role_permission (role_id, permission_id)
select r.id, p.id from app.role r cross join app.permission p
where r.code = 'ops'
  and (p.code like '%.view'
       or p.code in ('catalog.edit', 'costs.edit', 'inventory.edit', 'expenses.edit',
                     'import.run', 'sync.run'));

insert into app.setting_definition
  (key, category, description, value_type, default_value, validation, allowed_scopes, edit_permission)
values
  ('base_currency',            'general',  'Moneda base de los reportes',                           'string',  '"USD"',   '{"enum":["USD","MXN","EUR","CAD","GBP"]}', '{global}', 'settings.edit'),
  ('report_timezone',          'general',  'Zona horaria para agrupar por día',                     'string',  '"UTC"',   null, '{global,user}', 'settings.edit'),
  ('sync_window_days',         'sync',     'Días hacia atrás que trae cada sincronización',         'number',  '30',      '{"min":1,"max":365}', '{global,account}', 'settings.edit'),
  ('stale_sync_hours',         'sync',     'Horas sin sincronizar antes de alertar',                'number',  '26',      '{"min":1,"max":168}', '{global,account}', 'settings.edit'),
  ('count_only_settled',       'profit',   'Contar solo órdenes liquidadas (TikTok Shop)',          'boolean', 'true',    null, '{global,channel}', 'settings.edit'),
  ('order_date_basis',         'profit',   'Fecha con la que se atribuye una venta',                'string',  '"order_created"', '{"enum":["order_created","settled"]}', '{global,channel}', 'settings.edit'),
  ('missing_cogs_policy',      'profit',   'Qué hacer con SKU sin costo: cero o excluir',           'string',  '"zero"',  '{"enum":["zero","exclude"]}', '{global}', 'settings.edit'),
  ('expense_allocation_method','profit',   'Cómo se prorratean los gastos indirectos',              'string',  '"revenue"', '{"enum":["revenue","units","orders","equal"]}', '{global,channel}', 'settings.edit'),
  ('low_margin_threshold',     'alerts',   'Margen por debajo del cual se alerta',                  'number',  '0.10',    '{"min":-1,"max":1}', '{global,channel}', 'settings.edit'),
  ('alert_on_negative_profit', 'alerts',   'Alertar cuando un producto tiene utilidad negativa',    'boolean', 'true',    null, '{global}', 'settings.edit');

insert into app.alert_rule (code, name, rule_type, params, severity) values
  ('negative_profit_sku', 'Utilidad negativa por producto', 'negative_profit', '{"period_days":30}', 'warning'),
  ('low_margin_sku',      'Margen bajo por producto',       'low_margin',      '{"period_days":30}', 'info'),
  ('missing_cogs',        'SKU vendidos sin costo',          'missing_cogs',    '{}',                'warning'),
  ('sync_failed',         'Sincronización fallida',          'sync_failed',     '{}',                'critical'),
  ('sync_stale',          'Sincronización atrasada',         'sync_stale',      '{}',                'warning');
