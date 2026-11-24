// Datos de la pantalla Productos (backend: app/chartdata_productos.py, prefijo /api/chartdata).
import { authFetch, parse, query, type Channel } from '@/lib/api'

export interface ProductOverviewRow {
  sku: string
  product_name: string | null
  channels: Channel[]
  by_channel: Partial<Record<Channel, { units: number; gross_sales: number; net_profit: number }>>
  units: number
  gross_sales: number
  net_profit: number
  unit_profit: number | null
  margin: number | null
  avg_price: number | null
  /** Unidades por día en los últimos `velocity_days` días. */
  units_per_day: number
  weekly_units: number[]
  weekly_profit: number[]
}

export interface ProductsOverviewResponse {
  velocity_days: number
  weeks: number
  rows: ProductOverviewRow[]
}

export const chartdataProductos = {
  productsOverview: (f: { start?: string; end?: string; channel?: Channel | ''; account?: string }) =>
    authFetch(
      `/api/chartdata/products-overview${query({ start: f.start, end: f.end, channel: f.channel || undefined, account: f.account || undefined })}`,
    ).then((r) => parse<ProductsOverviewResponse>(r)),
}
