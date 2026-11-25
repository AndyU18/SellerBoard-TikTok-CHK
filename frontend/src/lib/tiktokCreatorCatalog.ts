// Tipos y llamadas del catálogo de afiliados de TikTok Creator (backend: app/tiktok_creator_api.py y
// app/tiktok_creator_analytics.py). Importado por pages/tiktok/TikTokCreatorOpportunities y TikTokCreatorInsights.
// Solo lectura (GET, permiso profit.view).
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/tiktok-creator'

/** Máximo de filas que acepta el backend por consulta. */
export const MAX_LIMIT = 200

export type OpportunitySort = 'score' | 'commission' | 'units' | 'price' | 'newest'

export interface EquivalentProduct {
  product_id: string
  title: string
  our_price: number | null
  their_price: number | null
  /** Diferencia de precio frente al nuestro, en porcentaje (15 = 15 % más caro). */
  price_difference_pct: number | null
  similarity: number
}

export interface CatalogRelation {
  sells_same_category: boolean
  our_products_in_category: number
  equivalent: EquivalentProduct | null
}

export interface CreatorOpportunity {
  product_id: string
  title: string | null
  image_url: string | null
  detail_link: string | null
  category: string | null
  category_leaf: string | null
  price: number | null
  original_price: number | null
  currency: string | null
  /** Fracción de 0 a 1 (0.1 = 10 %). */
  commission_rate: number | null
  commission_per_unit: number | null
  units_sold: number
  has_inventory: boolean
  shop_name: string | null
  sale_region: string | null
  suspect: boolean
  first_seen_at: string | null
  is_new: boolean
  affinity_groups: string[]
  /** Puntaje de 0 a 100 y sus cuatro factores. */
  score: number
  commission_factor: number
  demand_factor: number
  availability: number
  affinity: number
  relation: CatalogRelation
}

export interface ScoreConstants {
  commission_reference_usd: number
  demand_saturation_units: number
  demand_floor: number
  affinity: { category: number; title: number; none: number }
  affinity_keywords: Record<string, string[]>
}

export interface OpportunitiesResponse {
  total: number
  limit: number
  offset: number
  sort: OpportunitySort
  items: CreatorOpportunity[]
  score_formula: string
  constants: ScoreConstants
}

export interface OpportunityParams {
  q?: string
  category?: string
  /** Porcentaje (10 = 10 %). */
  minCommission?: number
  minUnits?: number
  sort: OpportunitySort
  limit: number
  offset: number
  includeSuspect: boolean
}

export interface CategorySummary {
  category: string
  products: number
  /** Fracción de 0 a 1. */
  avg_commission_rate: number | null
  units_sold: number
  avg_score: number
  affine: number
}

export interface ChangeRow {
  product_id: string
  title: string | null
  old: number | null
  new: number | null
  change_pct: number | null
}

export interface HistoryChanges {
  available: boolean
  reason?: string
  from?: string
  to?: string
  price_count?: number
  commission_count?: number
  price: ChangeRow[]
  /** `old` y `new` son tasas de comisión (fracción de 0 a 1). */
  commission: ChangeRow[]
}

export interface OpportunitiesSummary {
  products: number
  suspect_products: number
  avg_commission_rate: number | null
  by_category: CategorySummary[]
  new_this_week: { count: number; items: CreatorOpportunity[] }
  changes: HistoryChanges
  top: CreatorOpportunity[]
}

export interface CatalogCoverage {
  loaded: number
  total: number | null
  /** Fracción de 0 a 1; null si TikTok no informó el total. */
  pct: number | null
  visible_cap: number
  note: string
}

export interface CreatorStatus {
  configured: boolean
  connected: boolean
  needs_reauth: boolean
  message: string
  coverage: CatalogCoverage
}

const get = <T>(path: string, params: Record<string, string | undefined> = {}) =>
  authFetch(`${BASE}${path}${query(params)}`).then((r) => parse<T>(r))

const positive = (n: number | undefined) => (n != null && n > 0 ? String(n) : undefined)

export const tiktokCreator = {
  status: () => get<CreatorStatus>('/status'),
  summary: () => get<OpportunitiesSummary>('/opportunities/summary'),
  opportunities: (p: OpportunityParams) =>
    get<OpportunitiesResponse>('/opportunities', {
      q: p.q?.trim() || undefined,
      category: p.category || undefined,
      min_commission: positive(p.minCommission),
      min_units: positive(p.minUnits),
      sort: p.sort,
      limit: String(p.limit),
      offset: String(p.offset),
      include_suspect: p.includeSuspect ? 'true' : undefined,
    }),
}
