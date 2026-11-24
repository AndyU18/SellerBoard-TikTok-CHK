// Tipos, llamadas y cálculos puros de "Pagos, Retiros, Almacenes y Webhooks" de TikTok Shop
// (backend: app/tiktok_extra_api.py y app/tiktok_extra_analytics.py, prefijo /api/tiktok). Solo lectura.
// Lo importan las pantallas de pages/tiktok (TikTokPayments, TikTokWithdrawals, TikTokWarehouses, TikTokWebhooks).
import { authFetch, parse, query } from '@/lib/api'

/** Estado que TikTok usa para un pago ya depositado; cualquier otro cuenta como pendiente. */
export const PAID_STATUS = 'PAID'
/** Estados de pago que indican un problema (mismos que usa el Centro de acciones del backend). */
export const FAILED_PAYMENT_STATUSES: readonly string[] = ['FAILED', 'DECLINED', 'REJECTED', 'RETURNED', 'CANCELLED']

/** Cuadre de un pago contra las liquidaciones que cubre; sin `checkable` no hay diferencia calculada. */
export interface PaymentReconciliation {
  statements: number
  missing_statements: number
  settled_amount: number | null
  /** Pagado − liquidado (positivo = TikTok pagó de más). */
  difference: number | null
  reconciled: boolean
  checkable: boolean
}

export interface PaymentRow extends PaymentReconciliation {
  payment_id: string
  status: string | null
  currency: string | null
  amount: number
  /** Monto en USD que cubre las liquidaciones (contra esto se cuadra). */
  settlement_amount: number
  reserve_amount: number
  exchange_rate: number | null
  created_at: string | null
  paid_at: string | null
  /** Solo los últimos 4 dígitos de la cuenta; nunca el número completo. */
  bank_last4: string | null
}

export interface PaymentsSummary {
  payments: number
  by_status: Record<string, number>
  paid_amount: number
  pending_amount: number
  reserve_amount: number
  reconciliation: {
    checkable: number
    reconciled: number
    paid: number
    settled: number
    difference: number
    tolerance_usd: number
  }
}

export interface PaymentsResponse {
  available: boolean
  summary: PaymentsSummary
  payments: PaymentRow[]
}

export interface WithdrawalRow {
  withdrawal_id: string
  type: string | null
  status: string | null
  currency: string | null
  amount: number
  created_at: string | null
}

export interface WithdrawalMonth {
  month: string
  count: number
  amount: number
}

export interface WithdrawalsResponse {
  available: boolean
  summary: { withdrawals: number; by_status: Record<string, number>; total_amount: number }
  by_month: WithdrawalMonth[]
  withdrawals: WithdrawalRow[]
}

export interface WarehouseRow {
  warehouse_id: string
  name: string | null
  type: string | null
  sub_type: string | null
  status: string | null
  is_default: boolean | null
  city: string | null
  state: string | null
  region_code: string | null
  postal_code: string | null
}

export interface WarehousesResponse {
  available: boolean
  warehouses: WarehouseRow[]
}

export interface WebhookSubscription {
  event_type: string
  address: string | null
  created_at: string | null
}

export interface WebhooksResponse {
  subscriptions: WebhookSubscription[]
  documented_events: { event_type: string; subscribed: boolean }[]
  documented_events_note: string
  unsubscribed: string[]
}

export const tiktokMoney = {
  payments: (days: number) => authFetch(`/api/tiktok/payments${query({ days: String(days) })}`).then((r) => parse<PaymentsResponse>(r)),
  withdrawals: (days: number) => authFetch(`/api/tiktok/withdrawals${query({ days: String(days) })}`).then((r) => parse<WithdrawalsResponse>(r)),
  warehouses: () => authFetch('/api/tiktok/warehouses').then((r) => parse<WarehousesResponse>(r)),
  webhooks: () => authFetch('/api/tiktok/webhooks').then((r) => parse<WebhooksResponse>(r)),
}

// --- cálculos puros -------------------------------------------------------------------------------------

export const isPaid = (status: string | null | undefined): boolean => (status ?? '').toUpperCase() === PAID_STATUS
export const isFailed = (status: string | null | undefined): boolean => FAILED_PAYMENT_STATUSES.includes((status ?? '').toUpperCase())

/** "2026-11-24T10:00:00Z" -> "2026-11" (null si no hay fecha). */
export const monthOf = (iso: string | null | undefined): string | null => (iso && iso.length >= 7 ? iso.slice(0, 7) : null)

export interface PaymentMonth {
  month: string
  paid: number
  reserve: number
}

/** Pagado (solo estado PAID) y retenido por mes de creación, en orden cronológico. */
export function paymentsByMonth(rows: PaymentRow[]): PaymentMonth[] {
  const months = new Map<string, PaymentMonth>()
  for (const r of rows) {
    const month = monthOf(r.created_at)
    if (!month) continue
    const cur = months.get(month) ?? { month, paid: 0, reserve: 0 }
    if (isPaid(r.status)) cur.paid += r.amount
    cur.reserve += r.reserve_amount
    months.set(month, cur)
  }
  return [...months.values()].sort((a, b) => a.month.localeCompare(b.month))
}

/** Estado del cuadre de un pago: sin liquidaciones completas no se puede comparar. */
export type ReconcileState = 'ok' | 'difference' | 'unverifiable'
export const reconcileState = (p: PaymentReconciliation): ReconcileState =>
  !p.checkable ? 'unverifiable' : p.reconciled ? 'ok' : 'difference'

export interface ReconcileGaps {
  unverifiable: number
  /** Pagos que no traen ninguna liquidación enlazada. */
  withoutStatements: number
  /** Liquidaciones enlazadas que aún no están importadas. */
  missingStatements: number
}

export function reconcileGaps(rows: PaymentRow[]): ReconcileGaps {
  const open = rows.filter((r) => !r.checkable)
  return {
    unverifiable: open.length,
    withoutStatements: open.filter((r) => r.statements === 0).length,
    missingStatements: open.reduce((a, r) => a + r.missing_statements, 0),
  }
}

/** Monto retirado por estado (el backend solo da conteos por estado). */
export function withdrawalAmountByStatus(rows: WithdrawalRow[]): { status: string; amount: number }[] {
  const totals = new Map<string, number>()
  for (const w of rows) totals.set(w.status ?? '—', (totals.get(w.status ?? '—') ?? 0) + w.amount)
  return [...totals.entries()].map(([status, amount]) => ({ status, amount }))
}

export type WarehouseKind = 'sales' | 'returns' | 'other'
export function warehouseKind(type: string | null | undefined): WarehouseKind {
  const t = (type ?? '').toUpperCase()
  if (t.includes('RETURN')) return 'returns'
  if (t.includes('SALES')) return 'sales'
  return 'other'
}
