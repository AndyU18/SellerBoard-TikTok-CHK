// Tipos y llamadas de Rendimiento por producto, Rendimiento por SKU y Catálogo de TikTok Shop
// (backend: app/tiktok_extra_api.py y app/tiktok_extra_analytics.py). Importado por pages/tiktok/TikTokProductPerformance,
// TikTokSkuPerformance y TikTokCatalog. Solo lectura (GET, permiso profit.view).
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/tiktok'

export interface PerformanceWindow {
  start: string
  end: string
  snapshot_date: string
}

export type GmvSource = 'video' | 'live' | 'product_card'

export interface ProductPerformanceRow {
  rank: number
  product_id: string
  title: string | null
  thumb_url: string | null
  status: string | null
  gmv: number
  orders: number
  units_sold: number
  impressions: number | null
  page_views: number | null
  page_visitors: number | null
  click_through_rate: number | null
  conversion_rate: number | null
  /** Cada origen puede venir vacío: TikTok no siempre lo desglosa. */
  gmv_by_source: Record<GmvSource, number | null>
  profit: number | null
  settled_sales: number | null
  margin: number | null
  linked_to_master: boolean
  high_traffic_low_conversion: boolean
}

export interface TrendPoint {
  date: string
  gmv: number
  orders: number
  units_sold: number
}

export interface ProductPerformanceResponse {
  available: boolean
  window: PerformanceWindow | null
  summary: {
    products: number
    gmv: number
    orders: number
    units_sold: number
    impressions: number
    page_views: number
    store_conversion_rate: number | null
    profit: number
    high_traffic_views_threshold: number
    with_traffic_detail: number
  } | null
  products: ProductPerformanceRow[]
  traffic_low_conversion: ProductPerformanceRow[]
  trend: TrendPoint[]
}

export interface SkuPerformanceRow {
  rank: number
  sku_id: string
  seller_sku: string | null
  product_id: string | null
  title: string | null
  thumb_url: string | null
  price: number | null
  gmv: number
  orders: number
  units_sold: number
  master_sku: string | null
  master_name: string | null
  profit: number | null
  settled_sales: number | null
  profit_per_unit: number | null
  margin: number | null
  linked_to_master: boolean
}

export interface SkuPerformanceResponse {
  available: boolean
  window: PerformanceWindow | null
  summary: { skus: number; gmv: number; units_sold: number; unlinked: number } | null
  skus: SkuPerformanceRow[]
}

export interface CatalogProduct {
  product_id: string
  title: string | null
  image_url: string | null
  thumb_url: string | null
  image_count: number | null
  status: string | null
  audit_status: string | null
  audit_reasons: string[]
  listing_quality_tier: string | null
  is_not_for_sale: boolean | null
  brand: string | null
  categories: string[]
  sales_regions: string[]
  min_price: number | null
  max_price: number | null
  sku_count: number | null
  created_at: string | null
  updated_at: string | null
  gmv_30d: number
  settled_sales: number
  problem: boolean
}

export interface CatalogResponse {
  available: boolean
  summary: {
    products: number
    by_status: Record<string, number>
    by_quality: Record<string, number>
    by_audit: Record<string, number>
    problems: number
    problem_settled_sales: number
    without_image: number
    sales_days: number
  }
  problem_products: CatalogProduct[]
  products: CatalogProduct[]
}

const get = <T>(path: string, days: number) => authFetch(`${BASE}${path}${query({ days: String(days) })}`).then((r) => parse<T>(r))

export const tiktokCatalog = {
  productPerformance: (days: number) => get<ProductPerformanceResponse>('/product-performance', days),
  skuPerformance: (days: number) => get<SkuPerformanceResponse>('/sku-performance', days),
  catalog: (days: number) => get<CatalogResponse>('/catalog', days),
}
