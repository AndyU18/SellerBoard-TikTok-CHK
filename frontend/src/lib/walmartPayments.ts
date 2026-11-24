// Tipos y llamada de "Pagos y comisiones" de Walmart (backend: app/walmart_payments_api.py y
// app/walmart_payments.py). Importado por pages/walmart/WalmartPayments. Solo lectura.
import { authFetch, parse, query } from '@/lib/api'

export type AdjustmentKey = 'refunds' | 'shipping_labels'

/** Cifras de un mes o del total. La comisión estimada es inferida (ver `missing`). */
export interface PaymentsFigures {
  orders: number
  sales: number
  fees_actual: number
  fees_estimated: number
  fees: number
  /** Comisión total / ventas (0.15 = 15 %); null sin ventas. */
  effective_pct: number | null
  estimated_orders: number
  estimated_sales: number
  refunds: number
  shipping_labels: number
  /** Ventas − comisiones − reembolsos − etiquetas: lo esperado, no el pago confirmado. */
  net_expected: number
}

export interface PaymentsKpis extends PaymentsFigures {
  estimated_fees_pct: number | null
  estimated_orders_pct: number | null
  adjustments: number
}

export interface PaymentsResponse {
  days: number
  window: { start: string; end: string }
  assumed_commission_rate: number
  kpis: PaymentsKpis
  by_month: (PaymentsFigures & { month: string })[]
  by_adjustment: { key: AdjustmentKey; label: string; amount: number }[]
  /** Lo que no está guardado y por qué las cifras son parciales. */
  missing: string[]
}

export const walmartPayments = {
  report: (days: number) =>
    authFetch(`/api/walmart/payments${query({ days: String(days) })}`).then((r) => parse<PaymentsResponse>(r)),
}
