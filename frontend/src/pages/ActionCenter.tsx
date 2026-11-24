import { RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill } from '@/components/analytics/ui'
import { ChartCard, COLOR, EmptyChart, TipBox, axisMoney } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  actions,
  SNOOZE_OPTIONS,
  setActionStatus,
  type ActionEffort,
  type ActionItem,
  type ActionKindTotal,
  type ActionStatus,
  type SetStatusInput,
} from '@/lib/actions'
import { count, isoDate, money } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const DONE_WINDOW_DAYS = 7 // mismo criterio que el backend para "resueltas esta semana"
const DAY_MS = 86_400_000
const MAX_NOTE = 500

const EFFORT: Record<ActionEffort, { label: string; pill: string }> = {
  low: { label: 'Esfuerzo bajo', pill: PILL.green },
  medium: { label: 'Esfuerzo medio', pill: PILL.amber },
  high: { label: 'Esfuerzo alto', pill: PILL.red },
}

type Tab = ActionStatus
const TABS: { value: Tab; label: string }[] = [
  { value: 'open', label: 'Abiertas' },
  { value: 'snoozed', label: 'Pospuestas' },
  { value: 'done', label: 'Hechas' },
  { value: 'dismissed', label: 'Descartadas' },
]

const EMPTY_TEXT: Record<Tab, string> = {
  open: 'Sin acciones pendientes, buen trabajo.',
  snoozed: 'No hay acciones pospuestas.',
  done: 'Aún no marcas acciones como hechas.',
  dismissed: 'No hay acciones descartadas.',
}

/** Cambios que una actualización optimista aplica sobre la acción mientras la API responde. */
type Override = Pick<ActionItem, 'status' | 'note' | 'snoozed_until' | 'updated_at'>

// ---------------------------------------------------------------- cálculo (puro)
function applyOverrides(items: ActionItem[], overrides: Record<string, Override>): ActionItem[] {
  return items.map((i) => (overrides[i.key] ? { ...i, ...overrides[i.key] } : i))
}

function isRecentlyDone(item: ActionItem, now: number): boolean {
  if (item.status !== 'done' || !item.updated_at) return false
  const at = Date.parse(item.updated_at)
  return !Number.isNaN(at) && now - at <= DONE_WINDOW_DAYS * DAY_MS
}

function sumImpact(items: ActionItem[]): number {
  return items.reduce((s, i) => s + i.impact_usd, 0)
}

function totalsByKind(open: ActionItem[]): ActionKindTotal[] {
  const m = new Map<string, ActionKindTotal>()
  for (const i of open) {
    const t = m.get(i.kind) ?? { kind: i.kind, label: i.kind_label, count: 0, impact_usd: 0 }
    t.count += 1
    t.impact_usd += i.impact_usd
    m.set(i.kind, t)
  }
  return [...m.values()].sort((a, b) => b.impact_usd - a.impact_usd)
}

function snoozeUntil(days: number): string {
  return isoDate(new Date(Date.now() + days * DAY_MS))
}

function overrideFor(input: SetStatusInput): Override {
  return {
    status: input.status,
    note: input.note?.trim() || null,
    snoozed_until: input.status === 'snoozed' && input.snooze_days ? snoozeUntil(input.snooze_days) : null,
    updated_at: new Date().toISOString(),
  }
}

// ---------------------------------------------------------------- piezas de la pantalla
function KindChips({
  totals,
  selected,
  onSelect,
}: {
  totals: ActionKindTotal[]
  selected: string | null
  onSelect: (kind: string | null) => void
}) {
  return (
    <div role="group" aria-label="Filtrar por área" className="flex flex-wrap gap-2">
      <button
        type="button"
        aria-pressed={selected === null}
        onClick={() => onSelect(null)}
        className={`rounded-full border px-3 py-1 text-sm ${selected === null ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
      >
        Todas
      </button>
      {totals.map((t) => (
        <button
          key={t.kind}
          type="button"
          aria-pressed={selected === t.kind}
          onClick={() => onSelect(selected === t.kind ? null : t.kind)}
          className={`rounded-full border px-3 py-1 text-sm ${selected === t.kind ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
        >
          {t.label} · {count(t.count)} · {money(t.impact_usd)}
        </button>
      ))}
    </div>
  )
}

function KindChart({
  totals,
  selected,
  onSelect,
}: {
  totals: ActionKindTotal[]
  selected: string | null
  onSelect: (kind: string | null) => void
}) {
  return (
    <ChartCard
      title="Dinero en juego por área"
      subtitle="Estimación a 30 días de las acciones abiertas. Clic en una barra para filtrar la lista."
      filename="acciones-dinero-por-area"
      csv={totals.map((t) => ({ area: t.label, acciones: t.count, dinero_en_juego: Math.round(t.impact_usd * 100) / 100 }))}
    >
      {totals.length === 0 ? (
        <EmptyChart>Sin acciones abiertas.</EmptyChart>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(160, totals.length * 40)}>
          <BarChart data={totals} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" tickFormatter={axisMoney} tick={{ fontSize: 11 }} />
            <YAxis type="category" dataKey="label" width={130} tick={{ fontSize: 11 }} />
            <Tooltip
              cursor={{ fill: 'rgba(148,163,184,0.15)' }}
              content={({ active, payload }) => {
                const p = active ? (payload?.[0]?.payload as ActionKindTotal | undefined) : undefined
                return p ? (
                  <TipBox
                    title={p.label}
                    rows={[
                      { label: 'Dinero en juego', value: money(p.impact_usd), color: COLOR.bad },
                      { label: 'Acciones abiertas', value: count(p.count) },
                    ]}
                  />
                ) : null
              }}
            />
            <Bar
              dataKey="impact_usd"
              radius={[0, 3, 3, 0]}
              cursor="pointer"
              onClick={(row: unknown) => {
                const kind = (row as { payload?: ActionKindTotal }).payload?.kind
                if (kind) onSelect(selected === kind ? null : kind)
              }}
            >
              {totals.map((t) => (
                <Cell key={t.kind} fill={COLOR.bad} fillOpacity={selected && selected !== t.kind ? 0.3 : 1} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  )
}

/** Panel para confirmar un cambio de estado con nota opcional (y plazo si se pospone). */
function StatusComposer({
  status,
  busy,
  onConfirm,
  onCancel,
}: {
  status: Exclude<ActionStatus, 'open'>
  busy: boolean
  onConfirm: (input: SetStatusInput) => void
  onCancel: () => void
}) {
  const [note, setNote] = useState('')
  const [days, setDays] = useState<number>(SNOOZE_OPTIONS[1])
  const label = { done: 'Marcar como hecha', dismissed: 'Descartar', snoozed: 'Posponer' }[status]
  return (
    <div className="bg-muted/40 space-y-3 rounded-md border p-3" role="group" aria-label={label}>
      {status === 'snoozed' && (
        <div role="group" aria-label="Posponer por" className="flex flex-wrap items-center gap-1">
          <span className="text-muted-foreground mr-1 text-xs">Posponer</span>
          {SNOOZE_OPTIONS.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={days === d}
              onClick={() => setDays(d)}
              className={`rounded-md border px-2.5 py-1 text-xs ${days === d ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
            >
              {d} días
            </button>
          ))}
        </div>
      )}
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-muted-foreground text-xs">Nota (opcional)</span>
        <textarea
          value={note}
          maxLength={MAX_NOTE}
          rows={2}
          onChange={(e) => setNote(e.target.value)}
          className="border-input bg-background w-full rounded-md border px-2 py-1.5 text-sm"
          placeholder="Qué se hizo o por qué"
        />
      </label>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={busy}
          onClick={() => onConfirm({ status, note: note.trim() || undefined, snooze_days: status === 'snoozed' ? days : undefined })}
        >
          Confirmar
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancelar
        </Button>
      </div>
    </div>
  )
}

function ActionCard({
  item,
  maxImpact,
  busy,
  onNavigate,
  onChange,
}: {
  item: ActionItem
  maxImpact: number
  busy: boolean
  onNavigate?: (page: string) => void
  onChange: (key: string, input: SetStatusInput) => void
}) {
  const [composing, setComposing] = useState<Exclude<ActionStatus, 'open'> | null>(null)
  const effort = EFFORT[item.effort]
  const width = maxImpact > 0 ? Math.max(2, Math.round((item.impact_usd / maxImpact) * 100)) : 0

  const confirm = (input: SetStatusInput) => {
    setComposing(null)
    onChange(item.key, input)
  }

  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-medium">{item.title}</h3>
              <Pill label={item.kind_label} className={PILL.slate} />
              <Pill label={effort.label} className={effort.pill} />
            </div>
            <p className="text-muted-foreground text-sm">{item.detail}</p>
          </div>
          <div className="text-right">
            <div className="text-xl font-semibold tabular-nums">{money(item.impact_usd)}</div>
            <div className="text-muted-foreground text-xs">estimado</div>
          </div>
        </div>

        <div
          className="h-1.5 overflow-hidden rounded-full bg-slate-100"
          role="img"
          aria-label={`Impacto relativo: ${width}% de la acción más grande`}
        >
          <div className="h-full rounded-full bg-red-400" style={{ width: `${width}%` }} />
        </div>

        <details className="text-xs">
          <summary className="text-muted-foreground hover:text-foreground cursor-pointer">Cómo se calculó</summary>
          <p className="text-muted-foreground mt-1">{item.impact_basis}</p>
        </details>

        {item.status === 'snoozed' && item.snoozed_until && (
          <p className="text-xs text-amber-700">Pospuesta hasta el {item.snoozed_until}.</p>
        )}
        {item.status !== 'open' && item.note && <p className="text-xs italic">Nota: {item.note}</p>}
        {item.status !== 'open' && item.updated_by && (
          <p className="text-muted-foreground text-xs">Por {item.updated_by}</p>
        )}

        {composing ? (
          <StatusComposer status={composing} busy={busy} onConfirm={confirm} onCancel={() => setComposing(null)} />
        ) : (
          <div className="flex flex-wrap gap-2">
            {onNavigate && (
              <Button size="sm" variant="outline" onClick={() => onNavigate(item.target)}>
                Ir a la pantalla
              </Button>
            )}
            {item.status === 'open' ? (
              <>
                <Button size="sm" disabled={busy} onClick={() => setComposing('done')}>
                  Hecha
                </Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => setComposing('snoozed')}>
                  Posponer
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setComposing('dismissed')}>
                  Descartar
                </Button>
              </>
            ) : (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onChange(item.key, { status: 'open' })}>
                Reabrir
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function TabBar({ value, counts, onChange }: { value: Tab; counts: Record<Tab, number>; onChange: (t: Tab) => void }) {
  return (
    <div role="tablist" aria-label="Estado de las acciones" className="flex flex-wrap gap-1">
      {TABS.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={`rounded-md border px-3 py-1.5 text-sm ${value === t.value ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
        >
          {t.label} <span className="opacity-70">({counts[t.value]})</span>
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- pantalla
export function ActionCenter({ onNavigate }: { onNavigate?: (page: string) => void }) {
  const description = useScopeDescription()
  const [reload, setReload] = useState(0)
  const [now, setNow] = useState(() => Date.now()) // reloj fijo por carga: el render se mantiene puro
  const [tab, setTab] = useState<Tab>('open')
  const [kind, setKind] = useState<string | null>(null)
  const [overrides, setOverrides] = useState<Record<string, Override>>({})
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set())
  const [saveError, setSaveError] = useState<string | null>(null)

  const data = useAsync(() => actions.list('all'), [reload])
  const d = data.data

  const items = useMemo(() => applyOverrides(d?.items ?? [], overrides), [d, overrides])
  const byStatus = useMemo(() => {
    const m: Record<Tab, ActionItem[]> = { open: [], snoozed: [], done: [], dismissed: [] }
    for (const i of items) m[i.status].push(i)
    return m
  }, [items])
  const totals = useMemo(() => totalsByKind(byStatus.open), [byStatus.open])
  const doneThisWeek = useMemo(() => byStatus.done.filter((i) => isRecentlyDone(i, now)), [byStatus.done, now])

  const visible = useMemo(
    () =>
      byStatus[tab]
        .filter((i) => !kind || i.kind === kind)
        .sort((a, b) => b.impact_usd - a.impact_usd || a.key.localeCompare(b.key)),
    [byStatus, tab, kind],
  )
  const maxImpact = visible[0]?.impact_usd ?? 0
  const counts: Record<Tab, number> = {
    open: byStatus.open.length,
    snoozed: byStatus.snoozed.length,
    done: byStatus.done.length,
    dismissed: byStatus.dismissed.length,
  }

  const refresh = () => {
    setOverrides({})
    setSaveError(null)
    setNow(Date.now())
    setReload((n) => n + 1)
  }

  /** Aplica el cambio al instante y lo revierte si la API lo rechaza. */
  const change = (key: string, input: SetStatusInput) => {
    setSaveError(null)
    setOverrides((o) => ({ ...o, [key]: overrideFor(input) }))
    setBusy((b) => new Set(b).add(key))
    setActionStatus(key, input)
      .catch((e: unknown) => {
        setOverrides((o) => {
          const { [key]: _failed, ...rest } = o
          return rest
        })
        setSaveError(`No se pudo guardar el cambio: ${e instanceof Error ? e.message : String(e)}`)
      })
      .finally(() =>
        setBusy((b) => {
          const next = new Set(b)
          next.delete(key)
          return next
        }),
      )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Centro de acciones</h1>
          <p className="text-muted-foreground text-sm">{description}</p>
        </div>
        <Button size="sm" variant="outline" onClick={refresh} disabled={data.loading}>
          <RefreshCw className={data.loading ? 'animate-spin' : ''} /> Actualizar
        </Button>
      </div>
      <Hint>
        Lo que conviene hacer ahora, ordenado por dinero en juego. Los importes son estimaciones a 30 días (cada acción
        explica cómo se calculó) y las acciones cubren todo el negocio, sin importar los filtros de arriba.
      </Hint>

      <ErrorAlert message={data.error} />
      {saveError && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {saveError}
        </div>
      )}
      {d && d.errors.length > 0 && (
        <Callout>
          <strong>Algunas áreas no se pudieron calcular:</strong>{' '}
          {d.errors.map((e) => e.label).join(', ')}. Las demás acciones sí están al día.
        </Callout>
      )}
      {data.loading && !d && <p className="text-muted-foreground text-sm">Calculando acciones…</p>}

      {d && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi title="Acciones abiertas" value={count(byStatus.open.length)} />
            <Kpi title="Dinero en juego" value={money(sumImpact(byStatus.open))} hint="Estimado a 30 días" className="text-red-600" />
            <Kpi
              title="Resueltas esta semana"
              value={count(doneThisWeek.length)}
              hint={`${money(sumImpact(doneThisWeek))} de impacto`}
              className="text-emerald-600"
            />
            <Kpi title="Pospuestas" value={count(byStatus.snoozed.length)} hint={`${money(sumImpact(byStatus.snoozed))} en espera`} />
          </div>

          <KindChips totals={totals} selected={kind} onSelect={setKind} />
          <KindChart totals={totals} selected={kind} onSelect={setKind} />

          <section className="space-y-3">
            <TabBar value={tab} counts={counts} onChange={setTab} />
            <div role="tabpanel" aria-label={TABS.find((t) => t.value === tab)?.label} className="space-y-3">
              {visible.length === 0 ? (
                <p className="text-muted-foreground rounded-md border border-dashed p-6 text-center text-sm">
                  {kind && byStatus[tab].length > 0 ? 'Ninguna acción de esta área en esta pestaña.' : EMPTY_TEXT[tab]}
                </p>
              ) : (
                visible.map((item) => (
                  <ActionCard
                    key={item.key}
                    item={item}
                    maxImpact={maxImpact}
                    busy={busy.has(item.key)}
                    onNavigate={onNavigate}
                    onChange={change}
                  />
                ))
              )}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
