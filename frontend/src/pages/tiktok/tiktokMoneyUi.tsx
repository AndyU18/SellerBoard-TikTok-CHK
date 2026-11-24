// Piezas visuales compartidas por Pagos, Retiros y Almacenes de TikTok: insignia de estado y tabla vacía.
import { Pill, PILL } from '@/components/analytics/ui'
import { isFailed, isPaid } from '@/lib/tiktokMoney'

import { statusLabel } from './tiktokMoneyFormat'

const FINISHED_STATUSES = new Set(['SUCCESS', 'SUCCEEDED', 'COMPLETED', 'ENABLED'])

function statusTone(status: string | null | undefined): string {
  if (isPaid(status) || FINISHED_STATUSES.has((status ?? '').toUpperCase())) return PILL.green
  if (isFailed(status) || (status ?? '').toUpperCase() === 'DISABLED') return PILL.red
  return PILL.amber
}

export function StatusPill({ status }: { status: string | null | undefined }) {
  return <Pill label={statusLabel(status)} className={statusTone(status)} />
}

export function EmptyRows({ loading, message }: { loading: boolean; message: string }) {
  return <p className="text-muted-foreground py-6 text-center text-sm">{loading ? 'Cargando…' : message}</p>
}
