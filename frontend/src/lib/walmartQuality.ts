// Tipos y llamadas de Calidad del listado, Inventario WFS y Feeds de Walmart
// (backend: app/walmart_api.py y app/walmart_analytics.py). Importado por pages/walmart/WalmartListingQuality,
// WalmartWfs y WalmartFeeds. Solo lectura (GET, permiso profit.view).
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/walmart'
/** Máximo de cargas que devuelve /feeds. */
export const FEEDS_MAX_LIMIT = 200

export type QualityComponentKey =
  | 'offer_score'
  | 'rating_review_score'
  | 'content_score'
  | 'price_score'
  | 'shipping_score'
  | 'transactibility_score'

export interface QualityComponent {
  key: QualityComponentKey
  label: string
  score: number | null
  gap_to_100: number | null
}

export interface QualityLatest {
  snapshot_date: string
  listing_quality: number | null
  change_vs_previous: number | null
  /** Ordenados de peor a mejor. */
  components: QualityComponent[]
  defects: { item_defect_count: number | null; defect_ratio: number | null }
}

export type QualityHistoryPoint = {
  date: string
  listing_quality: number | null
} & Record<QualityComponentKey, number | null>

export interface ListingQualityResponse {
  days: number
  latest: QualityLatest | null
  history: QualityHistoryPoint[]
  item_level: { available: boolean; reason?: string }
}

export type WfsStatus = 'out' | 'low' | 'ok' | 'no_sales' | 'empty_no_sales'

export interface WfsRow {
  sku: string
  node: string | null
  avail_to_sell: number
  on_hand: number
  in_transit_or_reserved: number
  daily_units: number
  cover_days: number | null
  status: WfsStatus
  seller_fulfilled_stock: number | null
  stocked_elsewhere: boolean
  snapshot_at: string | null
}

export interface WfsInventoryResponse {
  days: number
  low_cover_days: number
  summary: {
    skus: number
    out_of_stock: number
    out_of_stock_total: number
    low_stock: number
    stocked_elsewhere: number
    avail_to_sell_units: number
    snapshot_at: string | null
  }
  rows: WfsRow[]
}

export interface FeedView {
  feed_id: string
  feed_type: string | null
  status: string | null
  items_received: number
  items_succeeded: number
  items_failed: number
  items_processing: number
  data_errors: number
  system_errors: number
  feed_date: string | null
}

export interface FeedTypeRow {
  feed_type: string
  feeds: number
  received: number
  succeeded: number
  failed: number
  success_rate: number | null
}

export interface FeedsResponse {
  days: number
  summary: {
    feeds: number
    items_received: number
    items_succeeded: number
    items_failed: number
    success_rate: number | null
    feeds_with_failures: number
  }
  by_type: FeedTypeRow[]
  with_failures: FeedView[]
  recent: FeedView[]
}

const get = <T>(path: string, params: Record<string, string | undefined>) =>
  authFetch(`${BASE}${path}${query(params)}`).then((r) => parse<T>(r))

export const walmartQuality = {
  listingQuality: (days: number) => get<ListingQualityResponse>('/listing-quality', { days: String(days) }),
  wfsInventory: (days: number, lowCoverDays?: number) =>
    get<WfsInventoryResponse>('/wfs-inventory', {
      days: String(days),
      low_cover_days: lowCoverDays == null ? undefined : String(lowCoverDays),
    }),
  feeds: (days: number, limit: number = FEEDS_MAX_LIMIT) =>
    get<FeedsResponse>('/feeds', { days: String(days), limit: String(limit) }),
}
