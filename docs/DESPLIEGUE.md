# Despliegue, copias de seguridad y calidad continua

Complementa la [guía local](GUIA_LOCAL.md). Aquí: correr la aplicación con Docker, respaldar los datos y qué
revisa la integración continua.

## 1. Con Docker (un solo comando)

Requiere Docker Desktop **encendido**. Desde la raíz del proyecto:

1. Crea `backend/.env` (plantilla: `backend/.env.example`). En producción necesitas al menos
   `CHK_DATABASE_URL` (Postgres/Supabase), `SUPABASE_URL` y las credenciales de las plataformas.
   **Sin `CHK_DATABASE_URL` el backend intenta usar SQLite, que la imagen de producción no incluye.**
2. Crea un archivo `.env` en la raíz con las dos variables públicas del frontend:
   ```
   VITE_SUPABASE_URL=https://TU-PROYECTO.supabase.co
   VITE_SUPABASE_PUBLISHABLE_KEY=la clave publishable
   ```
3. Arranca:
   ```bash
   docker compose up -d --build
   ```
4. Abre http://localhost:8080. El backend no publica puertos: solo nginx (frontend) le habla, por dentro de la red.

Qué hace cada pieza:

| Archivo | Para qué |
|---|---|
| `backend/Dockerfile` | Imagen de la API (Python 3.12, dependencias con `uv sync --frozen`, usuario sin privilegios, healthcheck en `/api/health`) |
| `frontend/Dockerfile` + `frontend/nginx.conf` | Compila con Vite y sirve con nginx; reenvía `/api` al backend (timeouts largos para sincronizar, archivos de hasta 25 MB) |
| `docker-compose.yml` | Une ambos y guarda `/data` (tokens renovados de TikTok y copias de seguridad) en un volumen |

> **Seguridad:** las credenciales de las plataformas solo viven en `backend/.env` y se leen al arrancar el
> contenedor; no se hornean en la imagen. Las `VITE_` van dentro del JavaScript del navegador, por eso solo se usa la
> clave *publishable*. **No ejecutes `docker compose config` sin `--quiet`: expande `env_file` e imprime todos los
> secretos.**

Comandos útiles: `docker compose logs -f backend`, `docker compose ps`, `docker compose down`.

## 2. Copias de seguridad

Dos capas, y conviene tener las dos:

1. **Las del proveedor (Supabase)**: actívalas en tu plan (copias diarias; con punto de restauración en planes de
   pago). Es la forma de recuperar usuarios, roles y permisos.
2. **La copia lógica de la aplicación** (`app.backup`): un `.zip` con una tabla por CSV de todo lo que modela la
   aplicación (ventas, catálogo, costos, devoluciones, metas, gastos...). Es portátil y **solo lee**, así que es seguro
   correrla en producción.

```bash
cd backend
uv run python -m app.backup create --out backups --keep 14   # crea una copia y conserva las 14 más nuevas
uv run python -m app.backup list --out backups
uv run python -m app.backup restore backups/chk-backup-20261123T120000Z.zip   # solo SQLite (ver abajo)
```

- **Restaurar** está soportado solo en SQLite (desarrollo): sin `--force` exige tablas vacías; con `--force` reemplaza
  todo. En Postgres/Supabase se restaura con `pg_restore` desde un volcado de `pg_dump` o con la restauración del
  proveedor, porque ahí hay identidades, secuencias y permisos que una copia lógica no debe tocar.
- La copia **no incluye** los usuarios de Supabase Auth ni sus roles.
- Las copias no se suben a git (`backups/` está en `.gitignore`); guárdalas en otro disco o en la nube privada.

**Programarla a diario**

- *Windows (Programador de tareas)*: acción `uv`, argumentos `run python -m app.backup create --out backups --keep 14`,
  carpeta de inicio `...\backend`.
- *Linux/Mac (cron)*: `0 3 * * * cd /ruta/backend && uv run python -m app.backup create --out backups --keep 14`
- *Docker*: `docker compose exec backend python -m app.backup create --out /data/backups --keep 14`

## 3. Integración continua (GitHub Actions)

`.github/workflows/ci.yml` corre en cada *push* a `main` y en cada *pull request*, sin credenciales:

| Trabajo | Qué revisa |
|---|---|
| `backend` | `uv sync --frozen`, `ruff check`, `pytest` (SQLite en memoria y servicios simulados) |
| `frontend` | `npm ci`, `npm run lint`, `npm run build` (incluye la revisión de tipos) |
| `docker` | Que las dos imágenes se construyan (después de que pasen los otros dos) |

Si quieres que nada llegue a `main` sin pasar la revisión, en GitHub: Settings → Branches → regla de protección →
"Require status checks to pass".

## 4. Pruebas de extremo a extremo

`backend/tests/test_e2e_journey.py` recorre el flujo completo **por HTTP** con la aplicación real y una base en
memoria: importar órdenes y costos, ver utilidad y alertas, revisar precios, exportar, fusionar un SKU duplicado y
previsualizar el resumen. Se corre con el resto: `uv run pytest`. No incluye pruebas del navegador (clics en la
interfaz); si las quieres, el siguiente paso natural es Playwright.

## 5. Lista antes de publicar en internet

- [ ] `CHK_AUTH_REQUIRED` **sin** definir (por defecto exige sesión) y registros abiertos desactivados en Supabase.
- [ ] HTTPS delante de nginx (un proxy o balanceador con certificado); el contenedor sirve HTTP.
- [ ] Credenciales rotadas si alguna se expuso alguna vez, y `backend/.env` fuera de git.
- [ ] Copias de seguridad programadas y una restauración de prueba hecha.
- [ ] El resumen por correo/Slack y el asistente con IA **apagados** salvo que decidas usarlos (ver la guía local).
