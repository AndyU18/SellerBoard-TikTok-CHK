// Tipos y llamada de "Precio mínimo con envío real" (backend: app/min_price_api.py y app/min_price.py).
// Importado por pages/MinPrice. Solo lectura. El piso usa el costo real de las etiquetas de Redo por estado.
import { authFetch, parse, query, type Channel } from '@/lib/api'

export type MinPriceStatus = 'below' | 'ok' | 'unreachable' | 'no_cost' | 'insufficient'
export type ShippingBasis = 'real' | 'estimated' | 'none'

export interface MinPriceState {
  state: string
  units: number
  /** Precio medio realmente cobrado en el estado (neto de promoción). */
  realized_price: number
  /** Envío por unidad con que se calculó el piso de este estado. */
  shipping_unit: number
  /** "state" = promedio real del estado; "sku" = pocas etiquetas, se usó el promedio del SKU. */
  shipping_basis: 'state' | 'sku'
  min_price: number | null
  break_even_price: number | null
  gap: number | null
  loses_money: boolean
}

export interface MinPriceRow {
  channel: string
  sku: string
  product_name: string | null
  units: number
  realized_price: number
  cogs_unit: number
  /** Comisión como fracción de la venta neta. */
  fee_rate: number
  refund_rate: number
  refund_unit: number
  shipping_unit: number
  shipping_basis: ShippingBasis
  labeled_units: number
  /** Precio mínimo con el margen objetivo; null si ningún precio lo alcanza. */
  min_price: number | null
  break_even_price: number | null
  max_state_min_price: number | null
  gap: number | null
  gap_pct: number | null
  below_units: number
  below_pct: number | null
  estimated_loss: number
  status: MinPriceStatus
  loss_states: string[]
  states: MinPriceState[]
}

export interface MinPriceSummary {
  skus: number
  below_skus: number
  estimated_loss: number
  skus_real_shipping: number
  skus_estimated_shipping: number
  skus_no_cost: number
  label_coverage: number | null
}

export interface MinPriceResponse {
  days: number
  target_margin: number
  min_units: number
  summary: MinPriceSummary
  rows: MinPriceRow[]
}

export interface MinPriceFilter {
  days: number
  targetMargin: number
  channel?: Channel | ''
  account?: string
}

/** Margen objetivo máximo que acepta la API (backend MAX_TARGET_MARGIN). */
export const MAX_TARGET_MARGIN = 0.9

export const minPrice = {
  report: (f: MinPriceFilter) =>
    authFetch(
      `/api/pricing/min-price${query({
        days: String(f.days),
        target_margin: String(f.targetMargin),
        channel: f.channel || undefined,
        account: f.account || undefined,
      })}`,
    ).then((r) => parse<MinPriceResponse>(r)),
}
