-- 0003: canales, cuentas, conectores, sincronización y capa bronce (datos crudos).

-- ---------------------------------------------------------------- canales y cuentas
create table sales.channel (
  code       text primary key,
  name       text not null,
  is_active  boolean not null default true
);
insert into sales.channel (code, name) values
  ('amazon',      'Amazon'),
  ('tiktok_shop', 'TikTok Shop'),
  ('walmart',     'Walmart');

create table ops.connector (
  id           bigint generated always as identity primary key,
  code         text not null unique,
  channel_code text not null references sales.channel (code),
  kind         text not null check (kind in ('sales_api', 'ads_api', 'csv')),
  display_name text not null,
  is_active    boolean not null default true
);
create index connector_channel_code_idx on ops.connector (channel_code);
insert into ops.connector (code, channel_code, kind, display_name) values
  ('tiktok_shop_api', 'tiktok_shop', 'sales_api', 'TikTok Shop Partner API'),
  ('tiktok_ads_api',  'tiktok_shop', 'ads_api',   'TikTok Business (Ads) API'),
  ('amazon_sp_api',   'amazon',      'sales_api', 'Amazon Selling Partner API'),
  ('amazon_ads_api',  'amazon',      'ads_api',   'Amazon Ads API'),
  ('walmart_api',     'walmart',     'sales_api', 'Walmart Marketplace API'),
  ('walmart_connect', 'walmart',     'ads_api',   'Walmart Connect (Ads)'),
  ('csv_import',      'amazon',      'csv',       'Importación CSV/Excel');

create table sales.account (
  id                  bigint generated always as identity primary key,
  channel_code        text not null references sales.channel (code),
  name                text not null,
  external_account_id text,                    -- Seller ID / shop id
  marketplace         text,                    -- ATVPDKIKX0DER, US, ...
  currency            char(3) not null default 'USD',
  connector_id        bigint references ops.connector (id),
  -- las credenciales NUNCA van en columnas normales: se guardan en Supabase Vault
  vault_secret_id     uuid,
  is_active           boolean not null default true,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (channel_code, name)
);
create index account_connector_id_idx on sales.account (connector_id);
create trigger account_touch before update on sales.account
  for each row execute function private.touch_updated_at();

-- ---------------------------------------------------------------- sincronización
create table ops.sync_job (
  id            bigint generated always as identity primary key,
  account_id    bigint not null references sales.account (id) on delete cascade,
  job_type      text not null,                 -- orders | finances | ads | listings | inventory
  schedule_cron text,                          -- null = solo manual
  enabled       boolean not null default true,
  params        jsonb not null default '{}',
  created_at    timestamptz not null default now(),
  unique (account_id, job_type)
);

create table ops.sync_run (
  id             bigint generated always as identity primary key,
  job_id         bigint references ops.sync_job (id) on delete set null,
  account_id     bigint references sales.account (id) on delete set null,
  job_type       text not null,
  triggered_by   text not null default 'schedule' check (triggered_by in ('schedule', 'manual', 'import')),
  triggered_user uuid,
  started_at     timestamptz not null default now(),
  finished_at    timestamptz,
  status         text not null default 'running'
                 check (status in ('running', 'success', 'partial', 'failed')),
  window_start   date,
  window_end     date,
  rows_fetched   integer not null default 0,
  rows_upserted  integer not null default 0,
  error          text,
  stats          jsonb not null default '{}'
);
create index sync_run_job_id_idx on ops.sync_run (job_id, started_at desc);
create index sync_run_account_id_idx on ops.sync_run (account_id, started_at desc);
create index sync_run_failed_idx on ops.sync_run (started_at desc) where status in ('failed', 'partial');

create table ops.data_quality_issue (
  id           bigint generated always as identity primary key,
  issue_type   text not null,                  -- missing_cogs | unmapped_sku | sync_failed | negative_price ...
  severity     text not null default 'warning' check (severity in ('info', 'warning', 'critical')),
  entity_type  text not null,                  -- product | order | account ...
  entity_ref   text not null,
  details      jsonb not null default '{}',
  detected_at  timestamptz not null default now(),
  resolved_at  timestamptz,
  resolved_by  uuid
);
create unique index data_quality_issue_open_uq
  on ops.data_quality_issue (issue_type, entity_type, entity_ref) where resolved_at is null;
create index data_quality_issue_detected_idx on ops.data_quality_issue (detected_at desc);

-- ---------------------------------------------------------------- bronce
-- Respuestas crudas de las APIs. Inmutables: solo se insertan. Reprocesar plata = leer de aquí.
create table bronze.raw_payload (
  id           bigint generated always as identity primary key,
  sync_run_id  bigint references ops.sync_run (id) on delete set null,
  account_id   bigint references sales.account (id) on delete set null,
  source       text not null,                  -- tiktok_shop | amazon_sp | walmart | tiktok_ads ...
  endpoint     text not null,                  -- /order/202309/orders/search ...
  external_id  text,                           -- id del objeto en la plataforma
  payload      jsonb not null,
  payload_hash text not null,                  -- sha256 del payload; evita duplicados idénticos
  fetched_at   timestamptz not null default now()
);
create unique index raw_payload_dedup_uq
  on bronze.raw_payload (source, coalesce(account_id, 0), endpoint, coalesce(external_id, ''), payload_hash);
create index raw_payload_sync_run_idx on bronze.raw_payload (sync_run_id);
create index raw_payload_account_fetched_idx on bronze.raw_payload (account_id, fetched_at desc);
create index raw_payload_lookup_idx on bronze.raw_payload (source, endpoint, external_id);

-- Archivos originales (CSV/Excel) guardados en Supabase Storage (bucket privado "raw-reports").
create table bronze.raw_file (
  id             bigint generated always as identity primary key,
  account_id     bigint references sales.account (id) on delete set null,
  source         text not null,
  report_type    text not null,                -- orders | ad_spend | cogs | settlement ...
  storage_bucket text not null default 'raw-reports',
  storage_path   text not null,
  filename       text not null,
  sha256         text not null,
  byte_size      bigint not null check (byte_size >= 0),
  uploaded_by    uuid,
  uploaded_at    timestamptz not null default now(),
  processed_at   timestamptz,
  sync_run_id    bigint references ops.sync_run (id) on delete set null,
  unique (sha256, report_type)
);
create index raw_file_account_idx on bronze.raw_file (account_id, uploaded_at desc);
create index raw_file_sync_run_idx on bronze.raw_file (sync_run_id);
