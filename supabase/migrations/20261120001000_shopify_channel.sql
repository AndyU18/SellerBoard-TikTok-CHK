-- 0010: canal Shopify (tienda propia) y su conector.
insert into sales.channel (code, name) values ('shopify', 'Shopify')
on conflict (code) do nothing;

insert into ops.connector (code, channel_code, kind, display_name) values
  ('shopify_api', 'shopify', 'sales_api', 'Shopify Admin API')
on conflict (code) do nothing;
