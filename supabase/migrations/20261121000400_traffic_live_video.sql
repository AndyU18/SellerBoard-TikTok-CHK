-- 0014: analytics de TikTok Shop: embudo de tráfico, sesiones LIVE y desempeño de videos.
--  * traffic_daily: una fila por día y origen. source = ALL (total de la tienda) | LIVE | VIDEO |
--    PRODUCT_CARD. Embudo: impresiones -> vistas de producto -> compradores/órdenes.
--  * live_session: una fila por transmisión LIVE (propia o de un creador) con sus ventas.
--  * video_performance: vistas, CTR y ventas por video en la ventana sincronizada (máx. 60 días).

create table sales.traffic_daily (
  id          bigint generated always as identity primary key,
  account_id  bigint not null references sales.account (id) on delete cascade,
  day         date not null,
  source      text not null check (source in ('ALL', 'LIVE', 'VIDEO', 'PRODUCT_CARD')),
  impressions integer,
  page_views  integer,
  visitors    integer,
  buyers      integer,
  orders      integer,          -- solo en ALL
  units       integer,          -- solo en ALL
  gmv         numeric(14, 4),
  imported_at timestamp with time zone not null default now(),
  unique (account_id, day, source)
);
create index traffic_daily_day_idx on sales.traffic_daily (day, account_id);

create table sales.live_session (
  id                  bigint generated always as identity primary key,
  account_id          bigint not null references sales.account (id) on delete cascade,
  external_id         text not null,
  username            text,
  title               text,
  start_at            timestamp with time zone,
  end_at              timestamp with time zone,
  gmv                 numeric(14, 4) not null default 0,
  gmv_24h             numeric(14, 4) not null default 0,
  orders              integer not null default 0,
  customers           integer not null default 0,
  units               integer not null default 0,
  products_added      integer not null default 0,
  products_sold       integer not null default 0,
  click_to_order_rate numeric(8, 4),
  avg_price           numeric(14, 4),
  imported_at         timestamp with time zone not null default now(),
  unique (account_id, external_id)
);
create index live_session_start_idx on sales.live_session (start_at);

create table sales.video_performance (
  id                 bigint generated always as identity primary key,
  account_id         bigint not null references sales.account (id) on delete cascade,
  video_id           text not null,
  username           text,
  title              text,
  posted_at          timestamp with time zone,
  views              integer,
  click_through_rate numeric(8, 4),
  gmv                numeric(14, 4) not null default 0,
  orders             integer not null default 0,
  units              integer not null default 0,
  window_start       date,
  window_end         date,
  imported_at        timestamp with time zone not null default now(),
  unique (account_id, video_id)
);
create index video_performance_username_idx on sales.video_performance (username);

alter table sales.traffic_daily      enable row level security;
alter table sales.live_session       enable row level security;
alter table sales.video_performance  enable row level security;
create policy read_sales_traffic_daily on sales.traffic_daily
  for select to authenticated using ((select private.has_permission('sales.view')));
create policy read_sales_live_session on sales.live_session
  for select to authenticated using ((select private.has_permission('sales.view')));
create policy read_sales_video_performance on sales.video_performance
  for select to authenticated using ((select private.has_permission('sales.view')));
grant select on sales.traffic_daily, sales.live_session, sales.video_performance to authenticated;
