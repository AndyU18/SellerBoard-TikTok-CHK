// Tipos y llamadas del Gestor de SKU y de la calidad de datos (backend: app/sku_api.py).
import { authFetch, parse, query } from '@/lib/api'

export interface SkuMember {
  sku: string
  name: string | null
  units: number
  sales: number
  channels: string[]
  has_cost: boolean
  listings: number
  aliases: string[]
  suggested: boolean
}

export interface DuplicateGroup {
  key: string
  suggested_sku: string
  total_sales: number
  total_units: number
  members: SkuMember[]
}

export interface DuplicatesResponse {
  days: number
  count: number
  groups: DuplicateGroup[]
}

export interface MergeResult {
  source_sku: string
  target_sku: string
  lines_moved: number
  lines_merged: number
  listings_moved: number
  ad_rows_moved: number
  cost_rows_moved: number
  cost_rows_dropped: number
  returns_updated: number
  affiliate_updated: number
  samples_updated: number
  samples_merged: number
  aliases_repointed: number
  gold_rows_deleted: number
  gold_rows: number
}

export interface SkuAliasRow {
  id: number
  alias: string
  sku: string
  product_name: string | null
  reason: string | null
  created_at: string | null
}

export type QualitySeverity = 'critical' | 'warning' | 'info' | 'ok'

/** Ids de pantalla a los que puede llevar una revisión (los resuelve App.tsx). */
export type QualityPage = 'costs' | 'skus' | 'reconciliation' | 'import' | 'alerts' | 'inventory'

export interface QualityCheck {
  id: string
  title: string
  severity: QualitySeverity
  count: number
  impact: number | null
  detail: string
  action: { page: QualityPage; label: string }
  /** SKU afectados (hasta 50); vacío si la revisión no es por SKU. */
  skus?: string[]
}

export interface DataQualityResponse {
  score: number
  summary: { critical: number; warning: number; info: number; ok: number; total: number; headline: string }
  checks: QualityCheck[]
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const skus = {
  duplicates: (days = 90) =>
    authFetch(`/api/skus/duplicates${query({ days: String(days) })}`).then((r) => parse<DuplicatesResponse>(r)),
  merge: (source_sku: string, target_sku: string, reason?: string) =>
    authFetch('/api/skus/merge', json({ source_sku, target_sku, reason })).then((r) => parse<MergeResult>(r)),
  aliases: () => authFetch('/api/skus/aliases').then((r) => parse<SkuAliasRow[]>(r)),
  addAlias: (alias: string, target_sku: string, reason?: string) =>
    authFetch('/api/skus/aliases', json({ alias, target_sku, reason })).then((r) => parse<SkuAliasRow>(r)),
  deleteAlias: (id: number) =>
    authFetch(`/api/skus/aliases/${id}`, { method: 'DELETE' }).then((r) => parse<{ deleted: number }>(r)),
  quality: () => authFetch('/api/data-quality').then((r) => parse<DataQualityResponse>(r)),
}
