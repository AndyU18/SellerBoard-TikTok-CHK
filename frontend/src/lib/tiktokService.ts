// Tipos y llamadas del servicio al cliente de TikTok Shop (solo lectura, prefijo /api/tiktok-service).
// Backend: app/tiktok_service_api.py + app/tiktok_service_analytics.py.
// Privacidad por diseño: el backend solo guarda cifras y metadatos (id opaco, horas, conteos, rol, categoría);
// aquí no existe ningún campo con texto de mensajes ni datos de compradores.
// Importado por pages/tiktok/TikTokCs* y tiktokServiceUi.
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/tiktok-service'

export type CsStatus = 'waiting_seller' | 'waiting_buyer' | 'closed'
export type CsCategory = 'complaint' | 'return_refund' | 'shipping_tracking' | 'order_status' | 'product_question' | 'other'
/** Rol de quien escribió el último mensaje. */
export type CsRole = 'buyer' | 'seller' | 'bot' | 'system'

/** Ventanas que acepta el backend (`days`). */
export const MIN_WINDOW_DAYS = 7
export const MAX_WINDOW_DAYS = 90
/** Máximo de filas por petición de la lista (MAX_LIST_LIMIT del backend). */
export const MAX_LIST_LIMIT = 500

// --- panorama ----------------------------------------------------------------------------------------

export interface CsResponseSummary {
  conversations_with_buyer_message: number
  responded: number
  /** 0-100. */
  response_rate_pct: number | null
  avg_seconds: number | null
  median_seconds: number | null
  p90_seconds: number | null
  /** 0-100, sobre las conversaciones con mensaje de comprador. */
  within_1h_pct: number | null
  within_24h_pct: number | null
}

export interface CsVolumeDay {
  date: string
  new_conversations: number
  buyer_messages: number
  seller_messages: number
  median_response_seconds: number | null
  pending_at_close: number
}

export interface CsCategoryCount {
  category: CsCategory
  conversations: number
}

/** Métricas OFICIALES de TikTok (agregadas por sesiones). Los porcentajes vienen ya en 0-100. */
export interface CsOfficialTotals {
  sessions: number
  response_percentage: number | null
  response_seconds: number | null
  satisfaction_percentage: number | null
  conversion_percentage: number | null
  guided_gmv: number
  days_with_data: number
}

/** Avance de la sincronización; `{}` si aún no corrió. */
export interface CsSyncState {
  conversations_listed?: number
  pending_remaining?: number
  initial_load_complete?: boolean
}

export interface CsOverview {
  available: boolean
  generated_at: string
  window_days: number
  open_conversations: number
  waiting_seller: number
  waiting_buyer: number
  unread_total: number
  conversations_in_window: number
  bot_only_conversations: number
  response: CsResponseSummary
  volume_by_day: CsVolumeDay[]
  categories: CsCategoryCount[]
  official: CsOfficialTotals
  sync: CsSyncState
}

// --- conversaciones ----------------------------------------------------------------------------------

/** Solo metadatos: no hay texto, nombre ni id de comprador. */
export interface CsConversation {
  conversation_id: string
  created_at: string | null
  last_message_at: string | null
  status: CsStatus
  category: CsCategory
  wait_hours: number | null
  unread_count: number
  message_count: number
  buyer_messages: number
  seller_messages: number
  bot_messages: number
  first_response_seconds: number | null
  last_sender_role: CsRole | null
  can_send_message: boolean | null
}

export interface CsConversationsPage {
  total: number
  limit: number
  offset: number
  items: CsConversation[]
}

export interface CsConversationsFilter {
  status?: CsStatus
  category?: CsCategory
  minWaitHours?: number
  limit?: number
  offset?: number
}

// --- tiempos de respuesta ----------------------------------------------------------------------------

export type CsSlot = {
  conversations: number
  responded: number
  median_seconds: number | null
}
export type CsHourSlot = CsSlot & { hour: number }
export type CsWeekdaySlot = CsSlot & { weekday: string }

export interface CsResponseTimes {
  window_days: number
  timezone: string
  summary: CsResponseSummary
  distribution: { bucket: string; count: number }[]
  by_hour: CsHourSlot[]
  by_weekday: CsWeekdaySlot[]
}

// --- categorías --------------------------------------------------------------------------------------

export interface CsCategoryRow {
  category: CsCategory
  conversations: number
  share_pct: number | null
  responded: number
  avg_response_seconds: number | null
  median_response_seconds: number | null
  unresolved: number
}

export interface CsCategories {
  window_days: number
  total_with_buyer_message: number
  categories: CsCategoryRow[]
}

// --- pendientes y agentes ----------------------------------------------------------------------------

export type CsBacklogBucket = {
  bucket: string
  count: number
  unread: number
}

export interface CsBacklog {
  generated_at: string
  total: number
  oldest_wait_hours: number | null
  buckets: CsBacklogBucket[]
}

export type CsAgentStatus = 'accepting_chats' | 'not_accepting_chats' | 'unknown'

export interface CsAgents {
  can_accept_chat: boolean | null
  status: CsAgentStatus
  updated_at: string | null
  waiting_seller: number
  oldest_wait_hours: number | null
  official_last_7_days: CsOfficialTotals
}

const get = <T>(path: string, params: Record<string, string | undefined> = {}) =>
  authFetch(`${BASE}${path}${query(params)}`).then((r) => parse<T>(r))

export const tiktokService = {
  overview: (days: number) => get<CsOverview>('/overview', { days: String(days) }),
  conversations: (f: CsConversationsFilter = {}) =>
    get<CsConversationsPage>('/conversations', {
      status: f.status,
      category: f.category,
      // `query` omite los valores vacíos; 0 horas equivale a "sin mínimo" para las que esperan.
      min_wait_hours: f.minWaitHours ? String(f.minWaitHours) : undefined,
      limit: String(f.limit ?? MAX_LIST_LIMIT),
      offset: String(f.offset ?? 0),
    }),
  responseTimes: (days: number) => get<CsResponseTimes>('/response-times', { days: String(days) }),
  categories: (days: number) => get<CsCategories>('/categories', { days: String(days) }),
  backlog: () => get<CsBacklog>('/backlog'),
  agents: () => get<CsAgents>('/agents'),
}
