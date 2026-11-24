import { Download } from 'lucide-react'
import { useMemo, useState } from 'react'
import { CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, Segmented } from '@/components/analytics/ui'
import { ChartCard, COLOR, EmptyChart, TipBox, axisMoney } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import {
  downloadCsv,
  walmartCatalog,
  type CsvRecord,
  type RepricerAssignedSku,
  type RepricerIssueType,
  type RepricerStrategy,
} from '@/lib/walmartCatalog'

const BUBBLE_SIZE = 90
const OK_KEY = 'ok'

const ISSUE: Record<RepricerIssueType, { label: string; pill: string; color: string; explain: string }> = {
  realized_below_floor: {
    label: 'Vendido bajo el piso',
    pill: PILL.red,
    color: COLOR.bad,
    explain: 'El precio al que realmente se vende está por debajo del precio mínimo real: cada venta pierde dinero.',
  },
  repricer_floor_below_min: {
    label: 'Piso del repricer bajo el mínimo real',
    pill: PILL.orange,
    color: '#f97316',
    explain: 'El repricer tiene un piso, pero es menor que lo que necesitas cobrar: puede bajar el precio hasta perder dinero.',
  },
  no_repricer_floor: {
    label: 'Sin piso en el repricer',
    pill: PILL.amber,
    color: COLOR.amber,
    explain: 'El SKU no tiene precio mínimo en el repricer: nada impide que lo baje por debajo de tu costo.',
  },
}

const ISSUE_ORDER: RepricerIssueType[] = ['realized_below_floor', 'repricer_floor_below_min', 'no_repricer_floor']

type View = 'all' | 'issues'
const VIEW_OPTIONS: { value: View; label: string }[] = [
  { value: 'issues', label: 'Con problema' },
  { value: 'all', label: 'Todos' },
]

/** Opciones booleanas de la estrategia, con su significado en texto legible. */
const STRATEGY_FLAGS: { key: keyof Pick<RepricerStrategy, 'enable_buybox_meet_external' | 'enable_for_promotion' | 'restore_seller_price_without_target' | 'compare_with_3p_offer_only'>; label: string }[] = [
  { key: 'enable_buybox_meet_external', label: 'Iguala precios externos para ganar el Buy Box' },
  { key: 'enable_for_promotion', label: 'Actúa también en promociones' },
  { key: 'restore_seller_price_without_target', label: 'Restaura tu precio si no hay objetivo' },
  { key: 'compare_with_3p_offer_only', label: 'Compara solo contra ofertas de terceros' },
]

const RULE_NAME_KEYS = ['strategyType', 'repricerStrategyType', 'type', 'name', 'strategy'] as const

/** Tipo legible de una regla cruda de Walmart (forma libre): primer campo de texto conocido. */
function ruleName(rule: unknown): string | null {
  if (typeof rule === 'string') return rule
  if (rule && typeof rule === 'object') {
    for (const key of RULE_NAME_KEYS) {
      const v = (rule as Record<string, unknown>)[key]
      if (typeof v === 'string' && v) return v
    }
  }
  return null
}

const strategyType = (s: RepricerStrategy): string => {
  const names = [...new Set(s.rules.map(ruleName).filter((n): n is string => n != null))]
  return names.length ? names.join(', ') : '—'
}

const issueGap = (a: RepricerAssignedSku) => a.issue?.gap ?? null

function csvRow(a: RepricerAssignedSku): CsvRecord {
  return {
    sku: a.sku,
    producto: a.product_name,
    estrategia: a.strategy,
    estado_repricer: a.repricer_status,
    precio_actual: a.price?.toFixed(2),
    precio_buybox: a.buybox_price?.toFixed(2),
    piso_repricer: a.repricer_min_price?.toFixed(2),
    piso_real: a.real_min_price?.toFixed(2),
    precio_realizado: a.realized_price?.toFixed(2),
    problema: a.issue ? ISSUE[a.issue.type].label : '',
    falta_para_el_piso: issueGap(a)?.toFixed(2),
  }
}

type FloorPoint = { id: string; label: string; x: number; y: number; kind: RepricerIssueType | typeof OK_KEY; gap: number | null }

/**
 * Precio realizado (x) contra precio mínimo real (y). La diagonal es el equilibrio: sobre ella el piso es
 * mayor que lo que se cobra. Verde = sin problema; los demás colores son el tipo de problema.
 */
function FloorBubble({ points, height = 340 }: { points: FloorPoint[]; height?: number }) {
  if (!points.length) return <EmptyChart>Ningún SKU asignado tiene precio realizado y piso real calculables.</EmptyChart>
  const top = Math.max(1, ...points.map((p) => Math.max(p.x, p.y)))
  const kinds = [...ISSUE_ORDER, OK_KEY] as const
  const color = (k: FloorPoint['kind']) => (k === OK_KEY ? COLOR.good : ISSUE[k].color)
  const label = (k: FloorPoint['kind']) => (k === OK_KEY ? 'Sin problema' : ISSUE[k].label)
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {kinds
          .filter((k) => points.some((p) => p.kind === k))
          .map((k) => (
            <span key={k} className="flex items-center gap-1.5">
              <span className="inline-block size-2.5 rounded-full" style={{ background: color(k) }} />
              {label(k)}
            </span>
          ))}
        <span className="text-muted-foreground flex items-center gap-1.5">
          <span className="inline-block h-0 w-5 border-t-2 border-dashed border-slate-400" />
          Equilibrio (cobrado = mínimo)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="x" name="Precio realizado" tickFormatter={axisMoney} tick={{ fontSize: 11 }} domain={[0, 'auto']} label={{ value: 'Precio realizado', position: 'insideBottom', offset: -4, fontSize: 11 }} />
          <YAxis type="number" dataKey="y" name="Precio mínimo real" tickFormatter={axisMoney} tick={{ fontSize: 11 }} width={60} domain={[0, 'auto']} label={{ value: 'Precio mínimo real', angle: -90, position: 'insideLeft', fontSize: 11 }} />
          <ZAxis type="number" dataKey="z" range={[BUBBLE_SIZE, BUBBLE_SIZE]} />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: top, y: top }]} stroke="#94a3b8" strokeDasharray="5 5" ifOverflow="extendDomain" />
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as FloorPoint | undefined) : undefined
              if (!p) return null
              return (
                <TipBox
                  title={p.label}
                  rows={[
                    { label: 'Precio realizado', value: money(p.x) },
                    { label: 'Precio mínimo real', value: money(p.y) },
                    { label: 'Falta para el piso', value: money(p.gap) },
                    { label: 'Estado', value: label(p.kind), color: color(p.kind) },
                  ]}
                />
              )
            }}
          />
          {kinds.map((k) => (
            <Scatter key={k} data={points.filter((p) => p.kind === k).map((p) => ({ ...p, z: 1 }))}>
              {points
                .filter((p) => p.kind === k)
                .map((p) => (
                  <Cell key={p.id} fill={color(k)} fillOpacity={0.7} />
                ))}
            </Scatter>
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

function StrategiesTable({ strategies }: { strategies: RepricerStrategy[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Estrategia</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead className="text-right">SKU asignados</TableHead>
          <TableHead>Reglas</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {strategies.map((s) => (
          <TableRow key={s.collection_id}>
            <TableCell className="font-medium">{s.strategy}</TableCell>
            <TableCell>{strategyType(s)}</TableCell>
            <TableCell>
              <Pill label={s.enabled ? 'Activa' : 'Inactiva'} className={s.enabled ? PILL.green : PILL.slate} />
            </TableCell>
            <TableCell className="text-right" title={`${count(s.assigned_in_report)} aparecen en el último reporte ITEM`}>
              {count(s.assigned_count)}
            </TableCell>
            <TableCell>
              <ul className="space-y-0.5 text-xs">
                {STRATEGY_FLAGS.map((f) => (
                  <li key={f.key} className={s[f.key] ? '' : 'text-muted-foreground'}>
                    <span aria-hidden="true">{s[f.key] ? '✓' : s[f.key] === false ? '✗' : '·'}</span>{' '}
                    <span className="sr-only">{s[f.key] ? 'Sí: ' : s[f.key] === false ? 'No: ' : 'Sin dato: '}</span>
                    {f.label}
                  </li>
                ))}
              </ul>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function AssignedTable({ rows }: { rows: RepricerAssignedSku[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>SKU</TableHead>
          <TableHead>Estrategia</TableHead>
          <TableHead className="text-right">Precio actual</TableHead>
          <TableHead className="text-right">Piso repricer</TableHead>
          <TableHead className="text-right">Piso real</TableHead>
          <TableHead className="text-right">Realizado</TableHead>
          <TableHead className="text-right">Falta</TableHead>
          <TableHead>Problema</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={8} className="text-muted-foreground py-8 text-center">
              Ningún SKU asignado tiene problemas.
            </TableCell>
          </TableRow>
        )}
        {rows.map((a) => (
          <TableRow key={a.sku}>
            <TableCell>
              <div className="font-mono text-xs">{a.sku}</div>
              {a.product_name && <div className="text-muted-foreground max-w-56 truncate text-xs">{a.product_name}</div>}
            </TableCell>
            <TableCell>{a.strategy}</TableCell>
            <TableCell className="text-right">{money(a.price)}</TableCell>
            <TableCell className="text-right">{money(a.repricer_min_price)}</TableCell>
            <TableCell className="text-right font-medium">{money(a.real_min_price)}</TableCell>
            <TableCell className="text-right">{money(a.realized_price)}</TableCell>
            <TableCell className={`text-right ${issueGap(a) != null ? 'font-medium text-red-700' : ''}`}>{money(issueGap(a))}</TableCell>
            <TableCell>
              {a.issue ? (
                <span title={ISSUE[a.issue.type].explain}>
                  <Pill label={ISSUE[a.issue.type].label} className={ISSUE[a.issue.type].pill} />
                </span>
              ) : (
                <span className="text-muted-foreground text-xs">Sin problema</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Repricer de Walmart: estrategias, SKU asignados y su cruce con el precio mínimo real (envío de Redo incluido). */
export function WalmartRepricer() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const [view, setView] = useState<View>('issues')
  const data = useAsync(() => walmartCatalog.repricer(days), [days])
  const report = data.data

  const assigned = useMemo(() => report?.assigned_skus ?? [], [report])
  const issues = useMemo(() => assigned.filter((a) => a.issue), [assigned])
  const visible = view === 'issues' ? issues : assigned

  const points = useMemo<FloorPoint[]>(
    () =>
      assigned.flatMap((a) =>
        a.realized_price != null && a.real_min_price != null
          ? [{ id: a.sku, label: a.sku, x: a.realized_price, y: a.real_min_price, kind: a.issue?.type ?? OK_KEY, gap: issueGap(a) }]
          : [],
      ),
    [assigned],
  )

  const gaps = issues.flatMap((a) => (issueGap(a) != null ? [issueGap(a) as number] : []))
  const avgGap = gaps.length ? gaps.reduce((s, g) => s + g, 0) / gaps.length : null
  const maxGap = gaps.length ? Math.max(...gaps) : null
  const summary = report?.summary
  const loading = data.loading && !report
  const placeholder = loading ? 'Cargando…' : '—'

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Repricer de Walmart</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
        <Hint>
          El riesgo: el repricer puede bajar el precio por debajo de lo que te deja ganar. Aquí se compara el piso que tiene configurado cada SKU contra el precio mínimo real, que incluye costo, comisión, reembolsos y el
          envío real de las etiquetas de Redo.
        </Hint>
      </header>

      <ErrorAlert message={data.error} />

      {report && <Callout tone={report.link.available ? 'info' : 'warning'}>{report.link.note} <span className="opacity-70">Fuente: {report.link.source}.</span></Callout>}

      <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Estrategias" value={summary ? count(summary.strategies) : placeholder} hint={summary ? `${count(summary.enabled)} activas` : undefined} />
        <Kpi title="SKU asignados" value={summary ? count(summary.assigned_total) : placeholder} hint={summary ? `${count(summary.assigned_in_report)} aparecen en el reporte ITEM` : undefined} />
        <Kpi
          title="SKU con problema"
          value={summary ? count(summary.with_issue) : placeholder}
          hint={summary ? `de ${count(summary.assigned_in_report)} analizados` : undefined}
          className={summary && summary.with_issue > 0 ? 'text-red-600' : ''}
        />
        <Kpi
          title="Brecha contra el mínimo real"
          value={summary ? money(avgGap) : placeholder}
          hint={maxGap != null ? `Promedio por SKU con problema · la mayor es ${money(maxGap)}` : 'Dólares que faltan para llegar al piso'}
          className={avgGap != null && avgGap > 0 ? 'text-red-600' : ''}
        />
      </section>

      {loading && <EmptyChart>Cargando…</EmptyChart>}

      {report && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Estrategias</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              {report.strategies.length === 0 ? <EmptyChart>No hay estrategias de repricer sincronizadas.</EmptyChart> : <StrategiesTable strategies={report.strategies} />}
            </CardContent>
          </Card>

          <ChartCard
            title="Precio realizado vs precio mínimo real"
            subtitle="Cada burbuja es un SKU asignado al repricer. Sobre la diagonal el piso real supera lo que se cobra: se pierde dinero."
            csv={points.map((p) => ({ sku: p.id, precio_realizado: p.x.toFixed(2), precio_minimo_real: p.y.toFixed(2), falta_para_el_piso: p.gap?.toFixed(2), estado: p.kind === OK_KEY ? 'Sin problema' : ISSUE[p.kind].label }))}
            filename="walmart-repricer-dispersion"
          >
            <FloorBubble points={points} />
          </ChartCard>

          <Card>
            <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
              <div>
                <CardTitle className="text-base">SKU asignados</CardTitle>
                <ul className="text-muted-foreground mt-1 space-y-0.5 text-xs">
                  {ISSUE_ORDER.map((t) => (
                    <li key={t}>
                      <strong>{ISSUE[t].label}:</strong> {ISSUE[t].explain}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Segmented label="Filtrar SKU" value={view} options={VIEW_OPTIONS.map((o) => ({ ...o, count: o.value === 'issues' ? issues.length : assigned.length }))} onChange={setView} />
                <Button type="button" variant="outline" size="sm" onClick={() => downloadCsv('walmart-repricer-sku.csv', visible.map(csvRow))} disabled={!visible.length}>
                  <Download className="mr-1 size-4" /> CSV
                </Button>
              </div>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <AssignedTable rows={visible} />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
