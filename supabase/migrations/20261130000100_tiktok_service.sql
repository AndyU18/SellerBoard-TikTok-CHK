-- TikTok Shop: servicio al cliente (solo lectura de la API), con PRIVACIDAD POR DISEÑO.
--  * tiktok_cs_conversation: una fila por conversación, SOLO cifras y metadatos. Nunca se guarda el texto de un
--    mensaje, ni nombres, ni ids de comprador, ni teléfonos, correos o direcciones. `category` se deriva del
--    texto en memoria (palabras clave) y el texto se descarta; el id de conversación es el opaco de TikTok.
--  * tiktok_cs_daily: historial diario (volumen, tiempos de respuesta) + métricas oficiales agregadas de TikTok.
--  * tiktok_cs_state: cursor de la carga inicial y ajustes de agentes (valores pequeños, sin personas).
-- Todo es un upsert idempotente por clave natural; RLS: lectura con el permiso profit.view.

create table ops.tiktok_cs_conversation (
  conversation_id         text primary key,
  created_at              timestamp with time zone not null,
  last_message_at         timestamp with time zone,
  last_sender_role        text,
  last_message_type       text,
  unread_count            integer not null default 0,
  can_send_message        boolean not null default true,
  message_count           integer not null default 0,
  buyer_messages          integer not null default 0,
  seller_messages         integer not null default 0,
  bot_messages            integer not null default 0,
  system_messages         integer not null default 0,
  type_counts             jsonb,
  day_counts              jsonb,
  category                text not null default 'other',
  first_buyer_at          timestamp with time zone,
  first_response_seconds  integer,
  waiting_since           timestamp with time zone,
  status                  text not null default 'closed'
                          check (status in ('waiting_seller', 'waiting_buyer', 'closed')),
  messages_complete       boolean not null default true,
  synced_at               timestamp with time zone not null default now()
);
create index tiktok_cs_conversation_created_idx on ops.tiktok_cs_conversation (created_at);
create index tiktok_cs_conversation_last_message_idx on ops.tiktok_cs_conversation (last_message_at);
create index tiktok_cs_conversation_status_idx on ops.tiktok_cs_conversation (status);
create index tiktok_cs_conversation_category_idx on ops.tiktok_cs_conversation (category);

create table ops.tiktok_cs_daily (
  day                               date primary key,
  new_conversations                 integer not null default 0,
  buyer_messages                    integer not null default 0,
  seller_messages                   integer not null default 0,
  responded_conversations           integer not null default 0,
  avg_response_seconds              integer,
  median_response_seconds           integer,
  pending_at_close                  integer,
  official_sessions                 integer,
  official_response_percentage      numeric(9, 4),
  official_response_seconds         integer,
  official_satisfaction_percentage  numeric(9, 4),
  official_conversion_percentage    numeric(9, 4),
  official_guided_gmv               numeric(14, 4),
  official_currency                 text,
  synced_at                         timestamp with time zone not null default now()
);

create table ops.tiktok_cs_state (
  key         text primary key,
  value       jsonb,
  updated_at  timestamp with time zone not null default now()
);

alter table ops.tiktok_cs_conversation enable row level security;
alter table ops.tiktok_cs_daily        enable row level security;
alter table ops.tiktok_cs_state        enable row level security;

create policy read_ops_tiktok_cs_conversation on ops.tiktok_cs_conversation
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_cs_daily on ops.tiktok_cs_daily
  for select to authenticated using ((select private.has_permission('profit.view')));
create policy read_ops_tiktok_cs_state on ops.tiktok_cs_state
  for select to authenticated using ((select private.has_permission('profit.view')));

grant select on ops.tiktok_cs_conversation, ops.tiktok_cs_daily, ops.tiktok_cs_state to authenticated;
