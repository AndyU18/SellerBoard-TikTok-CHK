// Tipos y llamada de "Utilidad por orden" (backend: app/order_profit_api.py y app/order_profit.py).
// Importado por pages/OrderProfit. Solo lectura. La utilidad es antes de publicidad (no se reparte por orden).
import { authFetch, parse, query } from '@/lib/api'

export type OrderSort = 'profit_asc' | 'profit_desc' | 'sales_asc' | 'sales_desc' | 'date_asc' | 'date_desc'
export type OrderDriverKey = 'cogs' | 'fees' | 'shipping' | 'refunds' | 'promo'

export interface OrderProfitRow {
  date: string
  order: string
  channel: string
  account: string
  state: string | null
  units: number
  gross_sales: number
  promo: number
  fees: number
  /** Envío total: el de las líneas más la etiqueta de Redo. */
  shipping: number
  label_cost: number
  refunds: number
  cogs: number
  net_sales: number
  profit: number
  margin: number | null
  driver: OrderDriverKey | null
  /** Frase completa, p. ej. "El envío se comió el margen". */
  driver_label: string | null
  /** Nombre corto de la causa, p. ej. "Envío". */
  driver_short: string | null
  skus: string[]
}

export interface OrderProfitResponse {
  /** Resumen de todo el filtro (no solo de la página y sin las facetas de clic). */
  summary: {
    orders: number
    loss_orders: number
    loss_pct: number | null
    loss_total: number
    profit_total: number
    avg_profit: number | null
  }
  histogram: { key: string; label: string; min: number | null; max: number | null; orders: number }[]
  by_driver: { key: OrderDriverKey; label: string; orders: number; amount: number }[]
  by_channel: { channel: string; orders: number; loss_orders: number; net_sales: number; profit: number }[]
  /** Órdenes que cumplen el filtro completo (incluye solo pérdidas y facetas). */
  total: number
  limit: number
  offset: number
  rows: OrderProfitRow[]
}

export interface OrderProfitFilter {
  start?: string
  end?: string
  channel?: string
  account?: string
  q?: string
  only_loss?: boolean
  sort?: OrderSort
  limit?: number
  offset?: number
  driver?: OrderDriverKey
  profit_min?: number
  profit_max?: number
}

export const orderProfit = {
  list: (f: OrderProfitFilter) => {
    const params: Record<string, string | undefined> = {}
    for (const [k, v] of Object.entries(f)) {
      if (v === undefined || v === null || v === '' || v === false) continue
      params[k] = String(v)
    }
    return authFetch(`/api/orders/profit${query(params)}`).then((r) => parse<OrderProfitResponse>(r))
  },
}
