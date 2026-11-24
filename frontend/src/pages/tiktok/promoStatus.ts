// Estados de las actividades de TikTok Shop: etiqueta, color y grupo (activa / programada / terminada).
import { PILL } from '@/components/analytics/ui'

import { humanize } from './tiktokFormat'

export type PromoGroup = 'active' | 'scheduled' | 'ended'

interface StatusStyle {
  label: string
  group: PromoGroup
  pill: string
  /** Color de la barra en la línea de tiempo. */
  bar: string
}

export const GROUP_STYLE: Record<PromoGroup, Pick<StatusStyle, 'pill' | 'bar'>> = {
  active: { pill: PILL.green, bar: '#10b981' },
  scheduled: { pill: PILL.blue, bar: '#3b82f6' },
  ended: { pill: PILL.slate, bar: '#94a3b8' },
}

const STATUS: Record<string, { label: string; group: PromoGroup }> = {
  ONGOING: { label: 'Activa', group: 'active' },
  NOT_START: { label: 'Programada', group: 'scheduled' },
  EXPIRED: { label: 'Terminada', group: 'ended' },
  DEACTIVATED: { label: 'Desactivada', group: 'ended' },
}

export const GROUP_LABEL: Record<PromoGroup, string> = { active: 'Activas', scheduled: 'Programadas', ended: 'Terminadas' }

/** Estado desconocido: se trata como terminado para no inflar las activas. */
export function statusStyle(status: string | null): StatusStyle {
  const known = status ? STATUS[status] : undefined
  const group = known?.group ?? 'ended'
  return { label: known?.label ?? humanize(status), group, ...GROUP_STYLE[group] }
}

export const promoGroup = (status: string | null): PromoGroup => statusStyle(status).group
