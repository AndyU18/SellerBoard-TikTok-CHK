// Tipos y llamada de la antigüedad del dinero pendiente de liquidar
// (backend: app/settlement_aging_api.py y app/settlement_aging.py). Solo lectura.
import { authFetch, parse, query } from '@/lib/api'

export type AgingOrigin = 'tiktok_shop' | 'affiliate'

export interface AgingBucket {
  key: string
  label: string
  min_days: number
  /** null = sin tope (la cubeta «60+»). */
  max_days: number | null
  /** true si la cubeta empieza después del plazo normal de liquidación. */
  overdue: boolean
  orders: number
  pending_usd: number
  tiktok_shop_orders: number
  tiktok_shop_usd: number
  affiliate_orders: number
  affiliate_usd: number
}

export interface AgingOriginRow {
  origin: AgingOrigin
  label: string
  orders: number
  pending_usd: number
  overdue_orders: number
  overdue_usd: number
}

export interface AgingOrder {
  order_id: string
  origin: AgingOrigin
  origin_label: string
  creator: string | null
  anchor_date: string
  /** Desde qué fecha corre el plazo: la entrega, el envío o (sin envío registrado) la orden. */
  anchor_kind: 'entrega' | 'envío' | 'orden'
  age_days: number
  bucket: string
  overdue: boolean
  estimated_usd: number
}

export interface DifferenceConcept {
  concept: string
  kind: string
  amount_usd: number
}

export interface SettlementDifference {
  settlement_id: number
  external_id: string
  statement_date: string | null
  payout_date: string | null
  status: string | null
  deposit_usd: number
  transactions_usd: number
  /** Depósito menos la suma de transacciones (negativo = TikTok depositó menos). */
  difference_usd: number
  /** Ajuste global que trae la liquidación sin desglosar. */
  adjustment_usd: number
  concepts: DifferenceConcept[]
}

export interface AgingResponse {
  as_of: string
  expected_days: number
  assumptions: {
    /** Comisión efectiva histórica (0.19 = 19%); null sin historial. */
    fee_rate: number | null
    avg_order_usd: number | null
    sample_orders: number
    note: string
  }
  totals: {
    orders: number
    pending_usd: number
    overdue_orders: number
    overdue_usd: number
    oldest_days: number
    difference_count: number
    difference_usd: number
  }
  buckets: AgingBucket[]
  by_origin: AgingOriginRow[]
  oldest_orders: AgingOrder[]
  differences: SettlementDifference[]
}

export const settlementAging = {
  get: (p: { limit?: number } = {}) =>
    authFetch(`/api/settlements/aging${query({ limit: p.limit?.toString() })}`).then((r) => parse<AgingResponse>(r)),
}
