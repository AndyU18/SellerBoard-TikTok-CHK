// Formatos de Pagos, Retiros y Almacenes de TikTok: etiquetas de estado, fechas y cuenta enmascarada.
export const NUM_CELL = 'text-right whitespace-nowrap tabular-nums'

const STATUS_LABEL: Record<string, string> = {
  PAID: 'Pagado',
  PENDING: 'Pendiente',
  PROCESSING: 'En proceso',
  FAILED: 'Fallido',
  DECLINED: 'Rechazado',
  REJECTED: 'Rechazado',
  RETURNED: 'Devuelto',
  CANCELLED: 'Cancelado',
  SUCCESS: 'Completado',
  SUCCEEDED: 'Completado',
  COMPLETED: 'Completado',
  ENABLED: 'Activo',
  DISABLED: 'Inactivo',
}

/** Texto en español de un estado de TikTok; los desconocidos se muestran tal cual. */
export const statusLabel = (status: string | null | undefined): string => {
  if (!status) return '—'
  return STATUS_LABEL[status.toUpperCase()] ?? status
}

/** "2026-11" -> "nov 2026". */
export function monthLabel(month: string): string {
  const [year, mm] = month.split('-').map(Number)
  return new Date(year, mm - 1, 1).toLocaleDateString('es', { month: 'short', year: 'numeric' })
}

/** Fecha corta ("24 nov 2026") a partir de un ISO; "—" si no hay. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('es', { day: '2-digit', month: 'short', year: 'numeric' })
}

/** Cuenta bancaria enmascarada: solo los últimos 4 dígitos. */
export const maskedAccount = (last4: string | null | undefined): string => (last4 ? `···· ${last4}` : '—')
