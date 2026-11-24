import { authFetch, parse, query, type Channel, type Filters } from '@/lib/api'

/** Artículos del pedido (backend/app/chartdata_pedidos.py): una fila por línea de orden. */
export interface OrderLineRow {
  id: number
  order: string
  date: string
  channel: Channel
  account: string
  sku: string
  product_name: string | null
  units: number
  gross_sales: number
  promo: number
  platform_fees: number
  shipping_cost: number
  refund_amount: number
  cogs: number
  net_profit: number
}

export interface OrderLinesResponse {
  total: number
  limit: number
  offset: number
  summary: {
    lines: number
    units: number
    gross_sales: number
    net_profit: number
  }
  rows: OrderLineRow[]
}

export const orderLines = (f: Filters, q: string, limit: number, offset: number) =>
  authFetch(
    `/api/chartdata/order-lines${query({ start: f.start, end: f.end, channel: f.channel, account: f.account, q: q || undefined, limit: String(limit), offset: String(offset) })}`,
  ).then((r) => parse<OrderLinesResponse>(r))
