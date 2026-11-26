import { CheckCircle2, Circle } from 'lucide-react'
import { useState } from 'react'

import { Callout, ErrorAlert, Pill, PILL } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuth } from '@/lib/auth'
import { markRotated, platformLabel, reopenRotation, security, type RotationRow } from '@/lib/security'
import { useAsync } from '@/lib/useAsync'

import { dateTime, EDIT_PERMISSION, NO_VALUE } from './securityFormat'
import { LoadingNote, PortalLink, ReadOnlyNotice, SecurityHeader } from './securityUi'

const NOTE_MAX_LENGTH = 500
const NOTE_PLACEHOLDER = 'Nota opcional (por ejemplo, "regenerada el lunes"). No escribas claves aquí.'
const REOPEN_CONFIRM = '¿Reabrir esta rotación? La plataforma volverá a quedar pendiente de rotar.'
const EXPOSED_REASON = 'Esta credencial se compartió en el chat con el asistente, así que se considera comprometida hasta que la cambies.'
const PRESENT_REASON = 'Hay credenciales de esta plataforma en el servidor: conviene cambiarlas si alguna vez salieron de ahí.'

/** Lo que cambia una fila al marcarla o reabrirla; se aplica en pantalla antes de que el servidor responda. */
type RotationState = Pick<RotationRow, 'rotated' | 'rotated_at' | 'rotated_by_email' | 'note'>
type Overrides = Readonly<Record<string, RotationState | undefined>>

const PENDING_STATE: RotationState = { rotated: false, rotated_at: null, rotated_by_email: null, note: null }

function ProgressBar({ done, total }: { done: number; total: number }) {
  const percent = total === 0 ? 100 : Math.round((done / total) * 100)
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="font-medium">
          {done} de {total} plataformas rotadas
        </span>
        <span className="text-muted-foreground tabular-nums">{percent}%</span>
      </div>
      <div
        role="progressbar"
        aria-label="Plataformas rotadas"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="h-2.5 overflow-hidden rounded-full bg-slate-200"
      >
        <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${percent}%` }} />
      </div>
    </div>
  )
}

function RotationStatus({ row }: { row: RotationRow }) {
  if (!row.rotated) return <Pill label="Pendiente" className={PILL.red} />
  return <Pill label="Rotada" className={PILL.green} />
}

interface CardActions {
  canEdit: boolean
  onMark: (platform: string, note: string | null) => Promise<void>
  onReopen: (platform: string) => Promise<void>
}

function RotationCard({ row, actions }: { row: RotationRow; actions: CardActions }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const { canEdit } = actions

  const mark = async () => {
    setBusy(true)
    await actions.onMark(row.platform, note.trim() || null)
    setBusy(false)
    setNote('')
  }
  const reopen = async () => {
    if (!window.confirm(REOPEN_CONFIRM)) return
    setBusy(true)
    await actions.onReopen(row.platform)
    setBusy(false)
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          {row.rotated ? <CheckCircle2 className="size-5 text-emerald-600" aria-hidden="true" /> : <Circle className="text-muted-foreground size-5" aria-hidden="true" />}
          {row.label}
        </CardTitle>
        <RotationStatus row={row} />
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">{row.exposed_in_chat ? EXPOSED_REASON : PRESENT_REASON}</p>
        {row.rotated && (
          <div>
            Rotada el {dateTime(row.rotated_at)} por {row.rotated_by_email ?? NO_VALUE}.
            {row.note && <span className="text-muted-foreground"> Nota: {row.note}</span>}
          </div>
        )}
        <div>
          <div className="font-medium">Pasos para rotarla</div>
          <ol className="mt-1 list-decimal space-y-1 pl-5">
            {row.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
        <PortalLink href={row.portal_url}>Abrir el portal de {row.label}</PortalLink>
        <div className="flex flex-wrap items-end gap-2">
          {!row.rotated && (
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={NOTE_MAX_LENGTH}
              disabled={!canEdit || busy}
              autoComplete="off"
              aria-label={`Nota de rotación de ${row.label}`}
              placeholder={NOTE_PLACEHOLDER}
              className="h-9 min-w-0 flex-1 rounded-md border px-3 text-sm disabled:opacity-50"
            />
          )}
          {row.rotated ? (
            <Button type="button" variant="outline" size="sm" onClick={reopen} disabled={!canEdit || busy}>
              Reabrir
            </Button>
          ) : (
            <Button type="button" size="sm" onClick={mark} disabled={!canEdit || busy}>
              {busy ? 'Guardando…' : 'Marcar como rotada'}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Lista de verificación de rotación: qué plataformas siguen con claves expuestas y cómo cambiarlas. */
export function SecurityRotation() {
  const { data, loading, error } = useAsync(() => security.rotation(), [])
  const { can } = useAuth()
  const canEdit = can(EDIT_PERMISSION)
  const [overrides, setOverrides] = useState<Overrides>({})
  const [actionError, setActionError] = useState<string | null>(null)

  const apply = (platform: string, state: RotationState | undefined) => setOverrides((cur) => ({ ...cur, [platform]: state }))
  const before = (platform: string) => overrides[platform]

  /** Aplica el cambio de inmediato y, si el servidor lo rechaza, vuelve al estado anterior. */
  const optimistic = async (platform: string, next: RotationState, send: () => Promise<RotationState | void>) => {
    const previous = before(platform)
    setActionError(null)
    apply(platform, next)
    try {
      const confirmed = await send()
      if (confirmed) apply(platform, confirmed)
    } catch (e) {
      apply(platform, previous)
      setActionError(`No se pudo guardar el cambio de ${platformLabel(platform)}: ${errorText(e)}`)
    }
  }

  const actions: CardActions = {
    canEdit,
    onMark: (platform, note) =>
      optimistic(platform, { rotated: true, rotated_at: new Date().toISOString(), rotated_by_email: null, note }, async () => {
        const saved = await markRotated(platform, note)
        return { rotated: true, rotated_at: saved.rotated_at, rotated_by_email: saved.rotated_by_email, note: saved.note }
      }),
    onReopen: (platform) =>
      optimistic(platform, PENDING_STATE, async () => {
        await reopenRotation(platform)
      }),
  }

  const rows = (data?.platforms ?? []).map((r) => ({ ...r, ...overrides[r.platform] }))
  const done = rows.filter((r) => r.rotated).length

  return (
    <div className="space-y-4">
      <SecurityHeader
        title="Rotación de credenciales"
        note="Cambiar una clave es la forma de cerrar una filtración: la clave vieja deja de servir. Sigue los pasos de cada plataforma y márcala como rotada."
      />
      <ErrorAlert message={error} />
      {loading && !data && <LoadingNote />}
      {!canEdit && <ReadOnlyNotice />}
      {actionError && <Callout tone="danger">{actionError}</Callout>}
      {data && (
        <>
          <ProgressBar done={done} total={rows.length} />
          {rows.length === 0 && <Callout tone="info">No hay plataformas con credenciales por rotar.</Callout>}
          {rows.map((row) => (
            <RotationCard key={row.platform} row={row} actions={actions} />
          ))}
        </>
      )}
    </div>
  )
}
