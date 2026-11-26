// Textos, colores y formatos compartidos por las pantallas de Seguridad. Solo metadatos: nunca valores de secretos.
import { PILL } from '@/components/analytics/ui'
import type { RiskLevel, ScopeLevel } from '@/lib/security'

/** Permiso que exige el backend para marcar rotaciones y renovar el token del vendedor. */
export const EDIT_PERMISSION = 'settings.edit'
export const NO_EDIT_PERMISSION_HINT = 'Tu usuario solo puede consultar esta pantalla: para hacer cambios necesitas el permiso de editar la configuración.'

/** Días restantes a partir de los cuales un vencimiento se marca en rojo / ámbar. */
export const EXPIRY_DANGER_DAYS = 7
export const EXPIRY_WARNING_DAYS = 30

export const NO_VALUE = '—'

export const dateTime = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString('es-US', { dateStyle: 'medium', timeStyle: 'short' }) : NO_VALUE

export const dateOnly = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleDateString('es-US', { dateStyle: 'medium' }) : NO_VALUE

export interface Badge {
  label: string
  className: string
}

export const LEVEL_BADGE: Readonly<Record<ScopeLevel, Badge>> = {
  read: { label: 'Lectura', className: PILL.green },
  write: { label: 'Escritura', className: PILL.amber },
  delete: { label: 'Eliminación', className: PILL.red },
}

export const LEVEL_ORDER: readonly ScopeLevel[] = ['read', 'write', 'delete']

export const LEVEL_EXPLANATION: Readonly<Record<ScopeLevel, string>> = {
  read: 'Solo consulta datos: la app los necesita para mostrarte las pantallas.',
  write: 'Puede crear o cambiar datos de la tienda. Esta app nunca lo hace.',
  delete: 'Puede borrar datos de la tienda. Esta app nunca lo hace.',
}

interface RiskStyle {
  title: string
  /** Colores de la tarjeta del semáforo. */
  box: string
  /** Color de la luz encendida. */
  light: string
}

export const RISK_STYLE: Readonly<Record<RiskLevel, RiskStyle>> = {
  low: { title: 'Riesgo bajo', box: 'border-emerald-300 bg-emerald-50 text-emerald-900', light: 'bg-emerald-500' },
  medium: { title: 'Riesgo medio', box: 'border-amber-300 bg-amber-50 text-amber-900', light: 'bg-amber-500' },
  high: { title: 'Riesgo alto', box: 'border-red-300 bg-red-50 text-red-900', light: 'bg-red-500' },
}

export const RISK_ORDER: readonly RiskLevel[] = ['high', 'medium', 'low']

/** Semáforo de un vencimiento: vencido o en pocos días = rojo, un mes = ámbar, resto = verde. */
export function expiryBadge(daysLeft: number | null, expired: boolean): Badge {
  if (expired) return { label: 'Vencido', className: PILL.red }
  if (daysLeft == null) return { label: 'Sin vencimiento', className: PILL.slate }
  const label = `${Math.max(0, Math.floor(daysLeft))} días`
  if (daysLeft <= EXPIRY_DANGER_DAYS) return { label, className: PILL.red }
  if (daysLeft <= EXPIRY_WARNING_DAYS) return { label, className: PILL.amber }
  return { label, className: PILL.green }
}

export const gitBadge = (ignored: boolean | null): Badge =>
  ignored === true
    ? { label: 'Git lo ignora', className: PILL.green }
    : ignored === false
      ? { label: 'Git NO lo ignora', className: PILL.red }
      : { label: 'Git: sin verificar', className: PILL.slate }

export const yesNo = (value: boolean): string => (value ? 'Sí' : 'No')
