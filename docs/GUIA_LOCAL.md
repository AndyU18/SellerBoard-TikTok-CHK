# Guía para correr CHK Profit en tu computadora

CHK Profit une las ventas de TikTok Shop, Walmart (y, cuando haya acceso, Amazon y Shopify) para responder
cuánto ganas por canal y por producto. Esta guía te lleva de cero a tener la aplicación corriendo en local.

## 1. Cómo está armado

```
SellerBoard-TikTok-CHK/
├─ backend/    API en Python (FastAPI + SQLAlchemy + Polars). Calcula la utilidad y habla con las plataformas.
├─ frontend/   Interfaz en React (Vite + Tailwind + shadcn/ui + Recharts).
├─ supabase/   Migraciones SQL (esquema de la base de datos en Supabase/Postgres).
├─ docs/       Esta guía.
└─ doc-xlsx-y-csv/   Excel de costos (landed cost) que se cargan desde la aplicación.
```

Los datos se organizan por capas: lo que llega de las plataformas se guarda en tablas **plata**
(`sales`, `catalog`, `finance`, `ops`) y de ahí se calcula la capa **oro** (`gold.daily_profit`) con la
utilidad diaria por canal y producto. La mayoría de las pantallas leen la capa oro o la plata directamente.

```
Plataformas (solo lectura) ──► conectores ──► capa plata ──► capa oro ──► API /api/* ──► pantallas
 TikTok · Walmart · Redo        backend/app/connectors        sales.*       gold.daily_profit
```

Utilidad por línea: `ventas − promociones − comisiones − envío − reembolsos − publicidad − costo del producto`.

## 2. Qué necesitas instalado

| Herramienta | Para qué | Cómo verificar |
|---|---|---|
| **Python 3.12 o más nuevo** y **uv** | Backend y pruebas | `uv --version` (instala uv desde https://docs.astral.sh/uv/) |
| **Node.js 20 o más nuevo** y npm | Frontend | `node --version` |
| **Git** | Código | `git --version` |
| Cuenta de **Supabase** (opcional) | Inicio de sesión y base de datos real | https://supabase.com |

uv descarga la versión correcta de Python por ti; no hace falta instalarla aparte.

## 3. Primer arranque

Abre **dos terminales** (PowerShell o Git Bash), una para el backend y otra para el frontend.

### 3.1 Backend (terminal 1)

```bash
cd backend
uv sync                      # instala las dependencias (la primera vez tarda un poco)
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env
uv run uvicorn app.main:app --reload
```

- La API queda en http://127.0.0.1:8000 y su documentación interactiva en http://127.0.0.1:8000/docs.
- Sin configurar nada usa **SQLite** (`backend/chk_local.db`) y crea las tablas solo: sirve para probar.
- Para usar tu base real de Supabase, descomenta `CHK_DATABASE_URL` en `.env` con la cadena del
  *Session pooler* (puerto 5432). Las tablas las crean las migraciones de `supabase/migrations/` (ver sección 7).

### 3.2 Frontend (terminal 2)

```bash
cd frontend
npm install
cp .env.example .env.local   # y escribe tus valores (ver abajo)
npm run dev
```

Abre http://localhost:5173. El servidor de desarrollo reenvía todo lo que empieza con `/api` al backend en el
puerto 8000, así que **el backend debe estar corriendo**.

`frontend/.env.local` necesita dos valores del panel de Supabase (Project Settings → API):

```
VITE_SUPABASE_URL=https://TU-PROYECTO.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=la clave "publishable" (anon)
```

Y en el backend, `SUPABASE_URL` con la misma URL para que valide la sesión.

### 3.3 Primer usuario

1. En Supabase → Authentication → URL Configuration, pon **Site URL** = `http://localhost:5173`.
2. En la pantalla de inicio de sesión crea tu cuenta. **El primer usuario registrado queda como Dueño** (acceso
   total); los siguientes quedan pendientes de aprobación hasta que el Dueño les asigne un rol.
3. Cuando ya hayas creado tu cuenta, desactiva los registros nuevos en Supabase (Authentication → Sign In / Up).

> La interfaz siempre pide una sesión de Supabase. Si solo quieres probar la API sin cuenta, pon
> `CHK_AUTH_REQUIRED=false` en `backend/.env`, reinicia el backend y usa http://127.0.0.1:8000/docs. **Solo en tu
> computadora: apagado el login cualquiera tiene acceso total.**

## 4. Cargar datos

**Datos de demostración (SQLite, sin plataformas):**

```bash
cd backend
uv run python -m app.seed    # datos SINTÉTICOS; se niega a correr si ya hay órdenes
```

**Datos reales:** descomenta en `backend/.env` las credenciales de cada plataforma (nombres en el archivo de
ejemplo; nunca pegues los valores en un chat ni los subas a git) y reinicia el backend. Después, en la
aplicación:

1. **Importar → Sincronizar** TikTok Shop, Walmart y Redo (todas son de **solo lectura**: la aplicación nunca
   escribe en ninguna plataforma).
2. **Aranceles → Cargar landed cost** con los Excel de `doc-xlsx-y-csv/` (reporte por contenedor y lista plana).
   Esto llena el costo (COGS) de cada producto; sin costo, la utilidad aparece inflada.
3. Opcional: **Importar** también acepta CSV de órdenes, publicidad, costos y etiquetas.

**Sincronización automática:** al arrancar, el backend sincroniza cada 6 horas las plataformas que tengan
credenciales. El estado de cada ejecución está en **Alertas → Sincronización automática**. Se apaga con
`CHK_AUTO_SYNC=false` y se ajusta con `CHK_AUTO_SYNC_INTERVAL_MINUTES` y `CHK_AUTO_SYNC_DAYS`.

## 5. Variables de entorno

Todas viven en `backend/.env` (plantilla: `backend/.env.example`, todo comentado). Las únicas del frontend son las
dos `VITE_` de arriba.

| Variable | Qué hace | Por defecto |
|---|---|---|
| `CHK_DATABASE_URL` | Base de datos. Vacía = SQLite local | SQLite |
| `SUPABASE_URL` | Valida los tokens de sesión | — |
| `CHK_AUTH_REQUIRED` | `false` = sin login (solo local) | `true` |
| `CHK_REPORT_TIMEZONE` | Zona horaria de Horas pico | `America/New_York` |
| `CHK_AUTO_SYNC`, `CHK_AUTO_SYNC_INTERVAL_MINUTES`, `CHK_AUTO_SYNC_DAYS` | Sincronización automática | activa, 360, 14 |
| `TTS_*` | TikTok Shop (clave, secreto, tokens) | — |
| `WMT_CLIENT_ID`, `WMT_CLIENT_SECRET` | Walmart Marketplace | — |
| `REDO_STORE_ID`, `REDO_API_KEY` | Redo (costo real de etiquetas) | — |

## 6. Qué hay en la aplicación

| Sección | Para qué sirve |
|---|---|
| **Resumen** | Resumen ejecutivo, Alertas, Pronóstico y metas, Estado de resultados (con gastos indirectos) |
| **Rentabilidad** | Pareto ABC, Versus (canal, tienda, producto), Precios (piso rentable y paridad), Aranceles, Promociones |
| **Operación** | Inventario, Reposición, Logística, Devoluciones (con costo real), Horas pico |
| **Dinero** | Flujo de caja (con proyección), Conciliación y fugas |
| **Creadores y tráfico** | Muestras (con decisiones por creador), Creativos, Embudo, LIVE |
| **Catálogo y datos** | Productos, Costos (COGS), Importar |

La barra **Vista** de arriba filtra todas las pantallas por canal y por tienda.

## 7. Base de datos en Supabase

Las migraciones están en `supabase/migrations/` y se aplican **en orden de nombre** (la 0001 a la 0016). Opciones:

- Con la CLI de Supabase: `supabase link` y `supabase db push`.
- A mano: pega cada archivo, en orden, en el *SQL Editor* de Supabase.

SQLite (desarrollo y pruebas) no usa las migraciones: el backend crea las tablas desde `app/models.py`.

## 8. Pruebas y calidad

```bash
cd backend
uv run pytest                 # ~160 pruebas, usan SQLite en memoria (no tocan tu base)
uv run ruff check .           # estilo y errores comunes

cd ../frontend
npm run lint                  # oxlint
npm run build                 # revisa tipos (tsc) y compila
```

## 9. Problemas frecuentes

| Síntoma | Qué hacer |
|---|---|
| La interfaz dice **“Falta configurar VITE_SUPABASE_URL…”** | Crea `frontend/.env.local` con las dos variables y reinicia `npm run dev` |
| Todo responde **401** | La sesión venció o `SUPABASE_URL` del backend no coincide con la del frontend |
| La interfaz carga pero no hay datos / errores de red | ¿Está corriendo el backend en el puerto 8000? Mira la terminal 1 |
| **Port 8000 already in use** | Cierra el otro proceso, o arranca con `--port 8001` y cambia el `proxy` en `frontend/vite.config.ts` |
| `TikTok Shop 98001021 … downstream server error` | Falla temporal del servidor de TikTok: reintenta en unos minutos (queda registrada en Alertas) |
| Una plataforma aparece **“Sin credenciales”** | Descomenta sus variables en `backend/.env` y reinicia el backend |
| La utilidad parece demasiado alta | Faltan costos: revisa la alerta “productos vendidos sin costo” y carga el landed cost |
| Quiero empezar de cero con SQLite | Borra `backend/chk_local.db` y vuelve a arrancar |

## 10. Seguridad

- Las plataformas se consultan en **solo lectura**; los conectores nunca crean, cambian ni borran nada allá.
- `.env`, `*.db` y `tts_tokens*.json` están en `.gitignore`: **nunca** los subas a git ni pegues su contenido en chats.
- Si una credencial se expuso, rótala en la plataforma y actualiza `backend/.env`.
- Antes de publicar la aplicación en internet: quita `CHK_AUTH_REQUIRED=false`, desactiva los registros abiertos en
  Supabase y usa un usuario de base de datos con los permisos mínimos.
