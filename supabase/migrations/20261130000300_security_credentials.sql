-- 0022: seguridad de credenciales y permisos.
-- La aplicación SOLO LEE de las plataformas; estas tablas guardan (1) cuándo se rotó cada credencial que se
-- expuso y (2) los NOMBRES de los permisos que concedió cada token, para saber cuáles sobran.
-- Nunca se guarda un valor secreto: ni tokens, ni claves, ni contraseñas.
--  * credential_rotation: una fila por plataforma YA rotada (sin fila = pendiente). Reabrir = borrar la fila.
--  * credential_scope_snapshot: foto de los permisos (scopes) de la plataforma; cada captura reemplaza la anterior.

create table ops.credential_rotation (
  platform          text primary key check (length(platform) between 1 and 64),
  rotated_at        timestamp with time zone not null default now(),
  rotated_by_email  text,
  note              text check (note is null or length(note) <= 500)
);

create table ops.credential_scope_snapshot (
  platform     text not null check (length(platform) between 1 and 64),
  scope        text not null check (length(scope) between 1 and 200),
  level        text not null check (level in ('read', 'write', 'delete')),
  captured_at  timestamp with time zone not null default now(),
  primary key (platform, scope)
);

alter table ops.credential_rotation       enable row level security;
alter table ops.credential_scope_snapshot enable row level security;

create policy read_ops_credential_rotation on ops.credential_rotation
  for select to authenticated using ((select private.has_permission('settings.view')));
create policy read_ops_credential_scope_snapshot on ops.credential_scope_snapshot
  for select to authenticated using ((select private.has_permission('settings.view')));

grant select on ops.credential_rotation, ops.credential_scope_snapshot to authenticated;
