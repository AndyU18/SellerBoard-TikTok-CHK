-- 0008: seguridad. RLS en todas las tablas, lectura según permisos, auditoría de cambios y
-- bucket privado de Storage.
-- Escritura: solo el backend (conexión directa, omite RLS). Los usuarios de la app únicamente
-- LEEN y solo lo que sus permisos permiten. bronze no tiene políticas: nadie de la app lo ve.

-- ---------------------------------------------------------------- lectura por permiso
do $$
declare
  t record;
begin
  for t in
    select * from (values
      ('sales.channel',                  'sales.view'),
      ('sales.account',                  'sales.view'),
      ('sales.sales_order',              'sales.view'),
      ('sales.order_line',               'sales.view'),
      ('sales.refund',                   'sales.view'),
      ('sales.financial_event',          'sales.view'),
      ('sales.settlement',               'sales.view'),
      ('sales.sales_tax_line',           'sales.view'),
      ('sales.ad_campaign',              'sales.view'),
      ('sales.ad_spend_daily',           'sales.view'),
      ('sales.fx_rate',                  'sales.view'),
      ('catalog.brand',                  'catalog.view'),
      ('catalog.category',               'catalog.view'),
      ('catalog.supplier',               'catalog.view'),
      ('catalog.product',                'catalog.view'),
      ('catalog.product_bundle_item',    'catalog.view'),
      ('catalog.channel_listing',        'catalog.view'),
      ('catalog.listing_price_history',  'catalog.view'),
      ('catalog.cost_component_type',    'costs.view'),
      ('catalog.product_cost_history',   'costs.view'),
      ('finance.expense_category',       'expenses.view'),
      ('finance.allocation_rule',        'expenses.view'),
      ('finance.expense',                'expenses.view'),
      ('finance.expense_allocation',     'expenses.view'),
      ('ops.inventory_location',         'inventory.view'),
      ('ops.inventory_snapshot',         'inventory.view'),
      ('ops.purchase_order',             'inventory.view'),
      ('ops.purchase_order_line',        'inventory.view'),
      ('ops.connector',                  'ops.view'),
      ('ops.sync_job',                   'ops.view'),
      ('ops.sync_run',                   'ops.view'),
      ('ops.data_quality_issue',         'ops.view'),
      ('gold.daily_profit',              'profit.view'),
      ('gold.refresh_log',               'profit.view'),
      ('app.setting_definition',         'settings.view'),
      ('app.setting_value',              'settings.view'),
      ('app.alert_rule',                 'settings.view'),
      ('app.role',                       'users.view'),
      ('app.permission',                 'users.view'),
      ('app.role_permission',            'users.view'),
      ('app.audit_log',                  'audit.view')
    ) as v(tbl, perm)
  loop
    execute format('alter table %s enable row level security', t.tbl);
    execute format(
      'create policy %I on %s for select to authenticated using ((select private.has_permission(%L)))',
      'read_' || replace(t.tbl, '.', '_'), t.tbl, t.perm
    );
    execute format('grant select on %s to authenticated', t.tbl);
  end loop;
end;
$$;

-- Tablas de bronce y datos propios de cada usuario: RLS activo.
alter table bronze.raw_payload enable row level security;   -- sin política = nadie de la app
alter table bronze.raw_file    enable row level security;

alter table app.profile      enable row level security;
alter table app.user_role    enable row level security;
alter table app.saved_view   enable row level security;
alter table app.notification enable row level security;

-- Cada usuario ve su perfil y sus roles; quien tiene users.view ve todos.
create policy read_app_profile on app.profile for select to authenticated
  using (user_id = (select auth.uid()) or (select private.has_permission('users.view')));
create policy read_app_user_role on app.user_role for select to authenticated
  using (user_id = (select auth.uid()) or (select private.has_permission('users.view')));

-- Vistas guardadas: las propias y las compartidas; cada quien edita solo las suyas.
create policy read_app_saved_view on app.saved_view for select to authenticated
  using (user_id = (select auth.uid()) or is_shared);
create policy insert_app_saved_view on app.saved_view for insert to authenticated
  with check (user_id = (select auth.uid()));
create policy update_app_saved_view on app.saved_view for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy delete_app_saved_view on app.saved_view for delete to authenticated
  using (user_id = (select auth.uid()));

-- Notificaciones: las propias y las generales; el usuario puede marcarlas como leídas.
create policy read_app_notification on app.notification for select to authenticated
  using (user_id is null or user_id = (select auth.uid()));
create policy update_app_notification on app.notification for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

grant select on app.profile, app.user_role, app.notification to authenticated;
grant select, insert, update, delete on app.saved_view to authenticated;
grant update (read_at) on app.notification to authenticated;

-- Vistas y funciones de lectura
grant select on catalog.product_cost_current to authenticated;
grant select on gold.monthly_profit to authenticated;
grant execute on function catalog.unit_cogs_on(bigint, date) to authenticated;

-- ---------------------------------------------------------------- auditoría de cambios
do $$
declare
  t text;
begin
  foreach t in array array[
    'app.setting_value', 'app.role', 'app.role_permission', 'app.user_role', 'app.profile',
    'app.alert_rule', 'catalog.product', 'catalog.channel_listing', 'catalog.supplier',
    'catalog.product_cost_history', 'catalog.product_bundle_item', 'finance.expense',
    'finance.allocation_rule', 'sales.account', 'ops.purchase_order', 'ops.sync_job'
  ]
  loop
    execute format(
      'create trigger audit_%s after insert or update or delete on %s
         for each row execute function private.audit_row()',
      replace(t, '.', '_'), t
    );
  end loop;
end;
$$;

-- ---------------------------------------------------------------- Storage
-- Bucket privado para los CSV/Excel originales (sin políticas = solo el backend con service role).
insert into storage.buckets (id, name, public, file_size_limit)
values ('raw-reports', 'raw-reports', false, 52428800)
on conflict (id) do nothing;
