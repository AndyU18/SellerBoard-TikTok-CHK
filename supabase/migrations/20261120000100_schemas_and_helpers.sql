-- 0001: esquemas y utilidades comunes.
-- Arquitectura medallion: bronze (crudo) -> silver (catalog/sales/finance/ops) -> gold (dashboard).
-- El backend FastAPI escribe con una conexión directa (rol postgres/service); los usuarios de
-- la app solo pueden LEER, y solo lo que sus permisos permiten (ver 0008).

create schema if not exists app;      -- usuarios, roles, permisos, configuración, auditoría
create schema if not exists bronze;   -- datos crudos inmutables (nunca visibles a usuarios)
create schema if not exists catalog;  -- productos, costos, listings
create schema if not exists sales;    -- cuentas, órdenes, eventos financieros, publicidad
create schema if not exists finance;  -- gastos y prorrateo
create schema if not exists ops;      -- inventario, compras, sincronización, calidad de datos
create schema if not exists gold;     -- tablas resumen para el dashboard
create schema if not exists private;  -- funciones internas (no expuestas)

revoke all on schema app, bronze, catalog, sales, finance, ops, gold, private from public, anon;
grant usage on schema app, catalog, sales, finance, ops, gold, private to authenticated;

create or replace function private.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;
