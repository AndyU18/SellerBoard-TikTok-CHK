import { authFetch, parse, query, type Filters, type GroupBy, type ProfitRow } from '@/lib/api'

/** Datos extra para gráficas (backend/app/chartdata_resumen.py). */
export interface ProductMonthRow {
  period: string
  sku: string
  product_name: string | null
  units: number
  gross_sales: number
  ad_spend: number
  refund_amount: number
  net_profit: number
}

export interface ProductMonthly {
  months: string[]
  rows: ProductMonthRow[]
}

export interface VersusSeriesResponse {
  interval: string
  rows: ProfitRow[]
}

export const chartdata = {
  versusSeries: (groupBy: GroupBy, interval: 'day' | 'month', f: Filters) =>
    authFetch(
      `/api/chartdata/versus-series${query({ group_by: groupBy, interval, start: f.start, end: f.end, channel: f.channel, account: f.account })}`,
    ).then((r) => parse<VersusSeriesResponse>(r)),
  productMonthly: (f: Filters, limit = 30) =>
    authFetch(
      `/api/chartdata/product-monthly${query({ start: f.start, end: f.end, channel: f.channel, account: f.account, limit: String(limit) })}`,
    ).then((r) => parse<ProductMonthly>(r)),
}
