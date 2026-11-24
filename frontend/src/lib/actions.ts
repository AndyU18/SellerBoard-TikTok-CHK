// Tipos y llamadas del Centro de acciones (backend: app/action_api.py y app/action_center.py).
// Importado por pages/ActionCenter. Las acciones se calculan en el servidor; lo único que se guarda es su estado.
import { authFetch, parse, query } from '@/lib/api'

export type ActionStatus = 'open' | 'done' | 'dismissed' | 'snoozed'
export type ActionEffort = 'low' | 'medium' | 'high'

export interface ActionItem {
  /** Clave estable "<kind>:<entidad>". */
  key: string
  kind: string
  kind_label: string
  title: string
  detail: string
  /** ESTIMACIÓN en USD; `impact_basis` dice cómo se calculó. */
  impact_usd: number
  impact_basis: string
  effort: ActionEffort
  /** Id de la pantalla donde se resuelve (p. ej. "redoCorrections"). */
  target: string
  entity: string | null
  meta: Record<string, unknown>
  status: ActionStatus
  note: string | null
  /** Fecha ISO (YYYY-MM-DD) hasta la que está pospuesta. */
  snoozed_until: string | null
  updated_at: string | null
  updated_by: string | null
}

export interface ActionKindTotal {
  kind: string
  label: string
  count: number
  impact_usd: number
}

export interface ActionProviderError {
  kind: string
  label: string
  error: string
}

export interface ActionsResponse {
  summary: {
    open: number
    open_impact_usd: number
    done_this_week: number
    done_this_week_impact_usd: number
    snoozed: number
    dismissed: number
  }
  by_kind: ActionKindTotal[]
  kinds: { kind: string; label: string }[]
  errors: ActionProviderError[]
  items: ActionItem[]
}

export interface SetStatusInput {
  status: ActionStatus
  note?: string
  snooze_days?: number
}

export const SNOOZE_OPTIONS: readonly number[] = [3, 7, 14, 30]

export const actions = {
  /** `all` trae todos los estados: la pantalla filtra por pestaña sin volver a pedir. */
  list: (status: ActionStatus | 'all' = 'all') =>
    authFetch(`/api/actions${query({ status })}`).then((r) => parse<ActionsResponse>(r)),
}

/** Cambia el estado de una acción ("open" la reabre). Lanza si la API la rechaza. */
export function setActionStatus(key: string, input: SetStatusInput) {
  return authFetch(`/api/actions/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }).then((r) => parse<{ key: string; status: ActionStatus; snoozed_until?: string | null }>(r))
}
