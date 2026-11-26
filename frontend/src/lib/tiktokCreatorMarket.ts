// Tipos y llamadas del mercado de creadores del vendedor de TikTok Shop (backend: app/tiktok_creator_market_api.py
// y app/tiktok_creator_market_analytics.py). Importado por pages/tiktok/TikTokMarketCreators, TikTokMarketCandidates,
// TikTokMarketCollaborations y TikTokMarketInsights. Solo lectura (GET, permiso profit.view): la app nunca invita,
// envía muestras ni crea colaboraciones.
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/tiktok-creators'

/** Máximo de filas por consulta que acepta el backend en /marketplace. */
export const MAX_LIMIT = 200
/** Máximo de la shortlist que acepta el backend. */
export const MAX_SHORTLIST = 50

export type MarketSort = 'score' | 'followers' | 'gmv' | 'newest'
export type CollabKind = 'open' | 'target' | 'all'
/** Relación del creador con nuestras muestras (la define el backend). */
export type CreatorRelation = 'none' | 'sampled_no_sales' | 'paused_by_policy' | 'already_working'
/** Recomendación: "Candidato", "Vigilar", "Bajo ajuste", "No enviar muestras" o "Ya trabajamos con él". */
export type MarketRecommendation = string

export interface ScorePart {
  name: string
  points: number
  max: number
  reason: string
}

export interface MarketCreator {
  creator_open_id: string
  username: string | null
  nickname: string | null
  avatar_url: string | null
  region: string | null
  followers: number
  size_tier: string
  categories: string[]
  category_ids: string[]
  /** Importe exacto o, si TikTok solo da rango, su piso: no mostrarlo como cifra si hay `gmv_range`. */
  gmv: number | null
  gmv_range: string | null
  units_sold_range: string | null
  /** Fracción de 0 a 1 en puntos base asumidos (unidad no documentada por TikTok); null si no se leyó la ficha. */
  engagement_rate: number | null
  gpm: number | null
  /** Fracción de crecimiento de seguidores entre fotos semanales; null sin historial. */
  follower_growth: number | null
  score: number
  score_parts: ScorePart[]
  recommendation: MarketRecommendation
  relation: CreatorRelation
  relation_reason: string | null
  is_candidate: boolean
  is_new: boolean
  first_seen_at: string
  last_seen_at: string
  has_detail: boolean
  /** Solo si la ficha del creador la trae. */
  brand_collaboration_count?: number | null
}

export interface CandidateCreator extends MarketCreator {
  /** Razón en una frase de por qué está en la shortlist. */
  reason: string
}

export interface MarketResponse<T> {
  total: number
  limit: number
  offset?: number
  rows: T[]
  notes: string[]
}

export interface MarketParams {
  q?: string
  category?: string
  /** Seguidores mínimos; 0 o vacío no filtra. */
  minFollowers?: number
  sort: MarketSort
  limit: number
  offset: number
}

export interface CategoryCount {
  category: string
  creators: number
}

export interface SizeCount {
  tier: string
  creators: number
}

export interface MarketSummary {
  creators: number
  candidates: number
  excluded: number
  new: number
  by_category: CategoryCount[]
  by_size: SizeCount[]
  top_candidates: MarketCreator[]
  notes: string[]
}

export interface Collaboration {
  kind: 'open' | 'target'
  id: string
  title: string | null
  status: string | null
  product_id: string | null
  product_status: string | null
  /** Fracción de 0 a 1; solo en abiertas. */
  commission_rate: number | null
  creators: number | null
  showcase_creators: number | null
  /** Creadores invitados; solo en dirigidas. */
  invited: number | null
  price_min?: number | null
  inventory?: number | null
  products?: number | null
  free_sample?: boolean | null
  start: string | null
  end: string | null
}

export interface CollaborationsResponse {
  kind: CollabKind
  status: string | null
  total: number
  /** Conteo por `tipo:estado` (p. ej. `open:NORMAL`). */
  counts: Record<string, number>
  rows: Collaboration[]
}

export interface RouteInfo {
  method: string
  path: string
  version: string
}

export interface MarketCoverage {
  creators: number
  creators_active: number
  creators_with_gmv: number
  creators_with_detail: number
  open_collaborations: number
  target_collaborations: number
}

export interface LastSync {
  status: string
  started_at: string
  finished_at: string | null
  stats: Record<string, unknown> | null
  error: string | null
}

export interface MarketStatus {
  routes: Record<string, RouteInfo>
  unavailable: string[]
  coverage: MarketCoverage
  last_sync: LastSync | null
  limits: { creator_page_size: number }
}

const get = <T>(path: string, params: Record<string, string | undefined> = {}) =>
  authFetch(`${BASE}${path}${query(params)}`).then((r) => parse<T>(r))

const positive = (n: number | undefined) => (n != null && n > 0 ? String(n) : undefined)

export const tiktokCreatorMarket = {
  status: () => get<MarketStatus>('/status'),
  summary: () => get<MarketSummary>('/marketplace/summary'),
  marketplace: (p: MarketParams) =>
    get<MarketResponse<MarketCreator>>('/marketplace', {
      q: p.q?.trim() || undefined,
      category: p.category || undefined,
      min_followers: positive(p.minFollowers),
      sort: p.sort,
      limit: String(p.limit),
      offset: String(p.offset),
    }),
  candidates: (limit: number) => get<MarketResponse<CandidateCreator>>('/candidates', { limit: String(limit) }),
  collaborations: (kind: CollabKind) => get<CollaborationsResponse>('/collaborations', { kind }),
}
