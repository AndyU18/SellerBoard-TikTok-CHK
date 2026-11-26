// Etiquetas, formatos y reglas de color compartidos por las pantallas de Servicio al cliente de TikTok.
// Todo opera sobre cifras y metadatos: nunca hay texto de mensajes ni datos de compradores.
import { PILL } from '@/components/analytics/ui'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { MAX_WINDOW_DAYS, MIN_WINDOW_DAYS, type CsCategory, type CsRole, type CsStatus } from '@/lib/tiktokService'

import { NO_VALUE } from './tiktokFormat'

/** Pestañas de este espacio a las que el panorama enlaza con `onOpenTab`. */
export type CsTabId = 'csConversations' | 'csResponseTimes' | 'csCategories' | 'csBacklog'

export const SELLER_CENTER_URL = 'https://seller-us.tiktok.com/'

export const CATEGORY_ORDER: readonly CsCategory[] = ['complaint', 'return_refund', 'shipping_tracking', 'order_status', 'product_question', 'other']

export const CATEGORY_LABEL: Record<CsCategory, string> = {
  complaint: 'Queja',
  return_refund: 'Devolución o reembolso',
  shipping_tracking: 'Envío y rastreo',
  order_status: 'Estado del pedido',
  product_question: 'Pregunta de producto',
  other: 'Otros',
}

export const STATUS_LABEL: Record<CsStatus, string> = {
  waiting_seller: 'Espera al vendedor',
  waiting_buyer: 'Espera al comprador',
  closed: 'Cerrada',
}

export const STATUS_TONE: Record<CsStatus, string> = {
  waiting_seller: PILL.red,
  waiting_buyer: PILL.blue,
  closed: PILL.slate,
}

const ROLE_LABEL: Record<CsRole, string> = { buyer: 'Comprador', seller: 'Vendedor', bot: 'Bot', system: 'Sistema' }

export const categoryLabel = (category: string) => CATEGORY_LABEL[category as CsCategory] ?? category
export const statusLabel = (status: string) => STATUS_LABEL[status as CsStatus] ?? status
export const roleLabel = (role: string | null) => (role ? (ROLE_LABEL[role as CsRole] ?? role) : NO_VALUE)

// --- tiempos -----------------------------------------------------------------------------------------

const SECONDS_PER_MINUTE = 60
const SECONDS_PER_HOUR = 3600
const HOURS_PER_DAY = 24

/** Segundos en la unidad más natural: `45 s`, `12 min`, `3.2 h`, `1.5 d`. */
export function durationText(seconds: number | null | undefined): string {
  if (seconds == null) return NO_VALUE
  if (seconds < SECONDS_PER_MINUTE) return `${Math.round(seconds)} s`
  if (seconds < SECONDS_PER_HOUR) return `${Math.round(seconds / SECONDS_PER_MINUTE)} min`
  const hours = seconds / SECONDS_PER_HOUR
  return hours < HOURS_PER_DAY ? `${hours.toFixed(1)} h` : `${(hours / HOURS_PER_DAY).toFixed(1)} d`
}

export const hoursText = (hours: number | null | undefined) => (hours == null ? NO_VALUE : durationText(hours * SECONDS_PER_HOUR))

/** Porcentaje que el backend ya entrega en escala 0-100. */
export const pctText = (value: number | null | undefined) => (value == null ? NO_VALUE : `${value.toFixed(1)}%`)

export const secondsToMinutes = (seconds: number | null) => (seconds == null ? null : Math.round((seconds / SECONDS_PER_MINUTE) * 10) / 10)

/** Semáforo de la espera de una conversación sin responder. */
const WAIT_WARN_HOURS = 6
const WAIT_DANGER_HOURS = 24
export function waitTone(hours: number | null | undefined): string {
  if (hours == null) return ''
  if (hours >= WAIT_DANGER_HOURS) return 'text-red-700'
  if (hours >= WAIT_WARN_HOURS) return 'text-amber-700'
  return 'text-green-700'
}

/** Semáforo de las que esperan al vendedor: ninguna = verde, hasta 5 = ámbar, más = rojo. */
const WAITING_WARN_COUNT = 1
const WAITING_DANGER_COUNT = 6
export function waitingCountTone(waiting: number): string {
  if (waiting >= WAITING_DANGER_COUNT) return 'text-red-700'
  if (waiting >= WAITING_WARN_COUNT) return 'text-amber-700'
  return 'text-green-700'
}

// --- ids ---------------------------------------------------------------------------------------------

const SHORT_ID_LENGTH = 8
/** Id opaco acortado a sus últimos caracteres: suficiente para reconocerlo, sin ser un dato personal. */
export const shortId = (id: string) => (id.length > SHORT_ID_LENGTH ? `…${id.slice(-SHORT_ID_LENGTH)}` : id)

// --- ventana -----------------------------------------------------------------------------------------

/** Días del período elegido arriba, ajustados al rango que acepta el backend (7 a 90). */
export function useWindowDays(): number {
  const { preset } = useScope()
  return Math.min(MAX_WINDOW_DAYS, Math.max(MIN_WINDOW_DAYS, daysFromPreset(preset)))
}
