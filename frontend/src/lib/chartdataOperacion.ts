// Series para las gráficas de operación (backend: app/chartdata_operacion.py, prefijo /api/chartdata).
import { authFetch, parse, query, type Filters } from '@/lib/api'

export interface ReturnsTrendResponse {
  granularity: 'week' | 'month'
  rows: {
    period: string
    returns: number
    returned_units: number
    units_sold: number
    return_rate: number | null
    refund: number
  }[]
}

export interface ReturnsMatrixResponse {
  granularity: 'week' | 'month'
  periods: string[]
  rows: { sku: string; product_name: string | null; values: number[]; total: number }[]
}

export interface ShippingBreakdownRow {
  key: string
  orders: number
  shipping: number
  net_sales: number
  shipping_per_order: number | null
  shipping_share: number | null
}

export interface ShippingBreakdownResponse {
  by_channel: ShippingBreakdownRow[]
  by_band: ShippingBreakdownRow[]
}

const get = <T>(path: string, f: Filters) =>
  authFetch(`/api/chartdata/${path}${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) =>
    parse<T>(r),
  )

export const chartdata = {
  returnsTrend: (f: Filters) => get<ReturnsTrendResponse>('returns-trend', f),
  returnsMatrix: (f: Filters) => get<ReturnsMatrixResponse>('returns-matrix', f),
  shippingBreakdown: (f: Filters) => get<ShippingBreakdownResponse>('shipping-breakdown', f),
}
