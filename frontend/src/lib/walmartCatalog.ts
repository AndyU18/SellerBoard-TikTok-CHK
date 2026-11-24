// Tipos y llamadas del catálogo de Walmart: repricer, plantillas de envío y reportes
// (backend: app/walmart_api.py y app/walmart_analytics.py). Solo lectura, permiso profit.view.
// Importado por pages/walmart/WalmartRepricer, WalmartShipping y WalmartReports.
import { authFetch, parse, query } from '@/lib/api'
import type { MinPriceStatus } from '@/lib/minPrice'

type Params = Record<string, string | number | undefined>

const get = <T>(path: string, params: Params = {}) => {
  const asText: Record<string, string | undefined> = {}
  for (const [k, v] of Object.entries(params)) asText[k] = v == null ? undefined : String(v)
  return authFetch(`${path}${query(asText)}`).then((r) => parse<T>(r))
}

// --- repricer ---------------------------------------------------------------------------------------

export type RepricerIssueType = 'realized_below_floor' | 'repricer_floor_below_min' | 'no_repricer_floor'

export interface RepricerIssue {
  type: RepricerIssueType
  /** Dólares que faltan para llegar al piso real; null si el repricer no tiene piso. */
  gap: number | null
}

export interface RepricerStrategy {
  collection_id: string
  strategy: string
  enabled: boolean
  assigned_count: number
  assigned_in_report: number
  enable_for_promotion: boolean | null
  restore_seller_price_without_target: boolean | null
  enable_buybox_meet_external: boolean | null
  compare_with_3p_offer_only: boolean | null
  /** Reglas tal como las devuelve Walmart (forma libre). */
  rules: unknown[]
}

export interface RepricerAssignedSku {
  sku: string
  product_name: string | null
  strategy: string
  repricer_status: string | null
  price: number | null
  buybox_price: number | null
  repricer_min_price: number | null
  repricer_max_price: number | null
  real_min_price: number | null
  realized_price: number | null
  floor_status: MinPriceStatus | null
  issue: RepricerIssue | null
}

export interface RepricerResponse {
  days: number
  link: { available: boolean; source: string; note: string }
  strategies: RepricerStrategy[]
  assigned_skus: RepricerAssignedSku[]
  summary: {
    strategies: number
    enabled: number
    assigned_total: number
    assigned_in_report: number
    with_issue: number
  }
}

// --- plantillas de envío ----------------------------------------------------------------------------

export interface ShippingMethod {
  ship_method: string | null
  status: string | null
  transit_days: number | null
  regions: (string | null)[]
  flat_charge: number | null
  charge_per_item: number | null
  charge_per_weight: number | null
  weight_unit: string | null
  /** Lo que la plantilla cobra al comprador por una orden de 1 artículo. */
  charged_per_single_item_order: number
  /** Costo promedio de etiqueta menos lo cobrado; positivo = lo absorbe el vendedor. */
  absorbed_vs_label: number | null
}

export interface ShippingTemplate {
  id: string
  name: string | null
  type: string | null
  rate_model_type: string | null
  status: string | null
  has_detail: boolean
  methods: ShippingMethod[]
  snapshot_at: string | null
}

export interface ShippingTemplatesResponse {
  days: number
  redo_label: { orders: number; avg_label_cost: number | null; note: string | null }
  comparison_note: string
  templates: ShippingTemplate[]
  summary: { templates: number; active: number; free_shipping_methods: number }
}

// --- reportes (Reports API) -------------------------------------------------------------------------

export type ReportType = 'ITEM' | 'BUYBOX'

export interface ReportRequest {
  request_id: string
  report_type: string | null
  status: string | null
  report_version: string | null
  submitted_at: string | null
  generated_at: string | null
}

export interface ReportsResponse {
  summary: { requests: number; by_status: Record<string, number> }
  requests: ReportRequest[]
}

export interface ReportColumnFill {
  name: string
  filled: number
  /** 0 a 1; null si el reporte no trae filas. */
  filled_pct: number | null
}

export type ReportCell = string | number | boolean | null

export interface ReportPreview {
  request_id: string
  report_type: string | null
  bytes: number
  headers: string[]
  rows: Record<string, ReportCell>[]
  preview_rows: number
  total_rows: number
  columns: ReportColumnFill[]
}

export const walmartCatalog = {
  repricer: (days: number) => get<RepricerResponse>('/api/walmart/repricer', { days }),
  shippingTemplates: (days: number) => get<ShippingTemplatesResponse>('/api/walmart/shipping-templates', { days }),
  reports: (limit?: number) => get<ReportsResponse>('/api/walmart/reports', { limit }),
  reportPreview: (requestId: string, limit: number) =>
    get<ReportPreview>(`/api/walmart/reports/${encodeURIComponent(requestId)}/preview`, { limit }),
}

// --- CSV ---------------------------------------------------------------------------------------------

export type CsvRecord = Record<string, string | number | boolean | null | undefined>

/** Descarga `rows` como CSV (UTF-8 con BOM para que Excel respete los acentos). No hace nada si no hay filas. */
export function downloadCsv(filename: string, rows: CsvRecord[]): void {
  if (!rows.length) return
  const cols = Object.keys(rows[0])
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const text = [cols.map(esc).join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
  const url = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
