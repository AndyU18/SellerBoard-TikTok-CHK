import { useMemo, useState } from 'react'

import { BreakEvenBubble, type BreakEvenPoint } from '@/components/analytics/BreakEvenBubble'
import { Callout, ErrorAlert, Hint, Kpi, NumberField, Pill, Segmented } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisMoney, ChartCard, COLOR, EmptyChart, PALETTE } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import {
  LIMITS,
  RECOMMENDATIONS,
  samplePolicy,
  type Recommendation,
  type SamplePolicyRow,
  type ScorePart,
} from '@/lib/samplePolicy'
import { useAsync } from '@/lib/useAsync'

const REC_COLOR: Record<Recommendation, string> = {
  'Seguir enviando': COLOR.good,
  'Enviar con tope': COLOR.blue,
  Pausar: COLOR.amber,
  Cortar: COLOR.bad,
}
const REC_PILL: Record<Recommendation, string> = {
  'Seguir enviando': 'bg-green-100 text-green-800',
  'Enviar con tope': 'bg-blue-100 text-blue-800',
  Pausar: 'bg-amber-100 text-amber-800',
  Cortar: 'bg-red-100 text-red-800',
}
const REC_ORDER = Object.fromEntries(RECOMMENDATIONS.map((r, i) => [r, i])) as Record<Recommendation, number>
const BUBBLE_GROUPS = Object.fromEntries(RECOMMENDATIONS.map((r) => [r, { label: r, color: REC_COLOR[r] }]))
const PART_COLOR: Record<ScorePart['key'], string> = {
  conversion: PALETTE[0],
  profit: PALETTE[2],
  speed: PALETTE[3],
  consistency: PALETTE[4],
}
const TABLE_ROWS_SHOWN = 200
const MAX_SCORE = 100

type SortKey = 'creator' | 'samples' | 'sample_cost' | 'orders' | 'sales' | 'profit_before_samples' | 'profit' | 'days_to_first_sale' | 'score' | 'recommendation'
type Filter = 'all' | Recommendation

function csvCell(v: string | number | null | undefined): string {
  const s = v == null ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function downloadCsv(name: string, rows: Record<string, string | number>[]) {
  if (!rows.length) return
  const cols = Object.keys(rows[0])
  const body = [cols, ...rows.map((r) => cols.map((c) => r[c]))].map((l) => l.map(csvCell).join(',')).join(String.fromCharCode(13, 10))
  const url = URL.createObjectURL(new Blob([String.fromCharCode(0xfeff) + body], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Texto del desglose de la puntuación (tooltip nativo y accesible por teclado vía `title`). */
const breakdownText = (r: SamplePolicyRow) =>
  `${r.score_parts.map((p) => `${p.label}: ${p.points.toFixed(1)} de ${p.weight}`).join(' · ')}`

/** Barra apilada: cada componente ocupa los puntos que aportó, sobre un total de 100. */
function ScoreBar({ row }: { row: SamplePolicyRow }) {
  return (
    <div className="flex items-center justify-end gap-2" title={breakdownText(row)}>
      <div className="flex h-2 w-16 overflow-hidden rounded bg-slate-100" aria-hidden="true">
        {row.score_parts.map((p) => (
          <span key={p.key} style={{ width: `${(p.points / MAX_SCORE) * 100}%`, background: PART_COLOR[p.key] }} />
        ))}
      </div>
      <span className="w-8 text-right font-medium tabular-nums">{row.score.toFixed(0)}</span>
    </div>
  )
}

function ScoreBreakdown({ row }: { row: SamplePolicyRow }) {
  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">{row.creator}</h2>
          <Pill label={row.recommendation} className={REC_PILL[row.recommendation]} />
          <span className="text-muted-foreground text-sm">{row.reason}</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {row.score_parts.map((p) => (
            <div key={p.key} className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>{p.label}</span>
                <span className="tabular-nums">
                  {p.points.toFixed(1)} / {p.weight}
                </span>
              </div>
              <div className="h-2 rounded bg-slate-100">
                <div className="h-2 rounded" style={{ width: `${p.value * 100}%`, background: PART_COLOR[p.key] }} />
              </div>
            </div>
          ))}
        </div>
        <p className="text-muted-foreground text-xs">
          Presupuesto sugerido: {row.suggested_samples} muestra(s) ({money(row.suggested_budget_usd)}). Primera muestra {row.first_sample}
          {row.first_sale ? `, primera venta ${row.first_sale}` : ', sin ventas'}.
        </p>
      </CardContent>
    </Card>
  )
}

export function SampleStrategy({ onNavigate }: { onNavigate?: (page: string) => void }) {
  const [maxSamples, setMaxSamples] = useState<number>(LIMITS.maxSamples.default)
  const [maxDays, setMaxDays] = useState<number>(LIMITS.maxDays.default)
  const [text, setText] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'score', dir: -1 })
  const { range } = useScope()
  const description = useScopeDescription()
  const start = range.start
  const end = range.end

  const data = useAsync(
    () => samplePolicy.report({ start, end, max_samples_without_sale: maxSamples, max_days_to_first_sale: maxDays }),
    [start, end, maxSamples, maxDays],
  )
  const d = data.data
  const rows = useMemo(() => d?.rows ?? [], [d])
  const s = d?.summary
  const isDefault = maxSamples === LIMITS.maxSamples.default && maxDays === LIMITS.maxDays.default

  const filtered = useMemo(() => {
    const needle = text.trim().toLowerCase()
    const list = rows.filter(
      (r) => (filter === 'all' || r.recommendation === filter) && (!needle || `${r.creator} ${r.content_type ?? ''}`.toLowerCase().includes(needle)),
    )
    const val = (r: SamplePolicyRow): string | number => {
      if (sort.key === 'creator') return r.creator.toLowerCase()
      if (sort.key === 'recommendation') return REC_ORDER[r.recommendation]
      return r[sort.key] ?? -Infinity
    }
    return [...list].sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * sort.dir)
  }, [rows, text, filter, sort])

  const chosen = rows.find((r) => r.creator === selected) ?? null

  const curveData = useMemo(() => (d?.curve ?? []).map((p) => ({ ...p, cap_label: `${p.cap}` })), [d])
  const curveSeries: ComboSeries[] = [
    { key: 'savings', label: 'Ahorro', type: 'bar', color: COLOR.good },
    { key: 'lost_profit', label: 'Utilidad en riesgo', type: 'bar', color: COLOR.bad },
    { key: 'false_positives', label: 'Falsos positivos', type: 'line', axis: 'right', color: COLOR.blue, format: (v) => count(v) },
  ]
  const histData = useMemo(() => (d?.histogram ?? []).map((b) => ({ range: `${b.from}-${b.to}`, count: b.count })), [d])

  const bubble: BreakEvenPoint[] = useMemo(
    () =>
      rows
        .filter((r) => r.sample_cost > 0)
        .map((r) => ({
          id: r.creator,
          label: r.creator,
          x: r.sample_cost,
          y: Math.max(0, r.profit_before_samples),
          z: Math.round(r.sales),
          group: r.recommendation,
          extra: [
            { label: 'Puntuación', value: r.score.toFixed(0) },
            { label: 'Recomendación', value: r.recommendation },
          ],
        })),
    [rows],
  )

  const th = (key: SortKey, label: string, right = true) => (
    <TableHead className={right ? 'text-right' : ''}>
      <button type="button" className="hover:underline" onClick={() => setSort((p) => ({ key, dir: p.key === key ? (p.dir === 1 ? -1 : 1) : -1 }))}>
        {label}
        {sort.key === key ? (sort.dir === -1 ? ' ↓' : ' ↑') : ''}
      </button>
    </TableHead>
  )

  const csv = filtered.map((r) => ({
    Creador: r.creator,
    Recomendación: r.recommendation,
    Razón: r.reason,
    Puntuación: r.score.toFixed(1),
    ...Object.fromEntries(r.score_parts.map((p) => [`Puntos ${p.label}`, p.points.toFixed(1)])),
    Muestras: r.samples,
    'Costo de muestras': r.sample_cost.toFixed(2),
    Órdenes: r.orders,
    Ventas: r.sales.toFixed(2),
    'Utilidad antes de muestras': r.profit_before_samples.toFixed(2),
    'Utilidad después de muestras': r.profit.toFixed(2),
    'Días a la primera venta': r.days_to_first_sale ?? '',
    'Tipo de contenido': r.content_type ?? '',
    'Órdenes por muestra': r.sample_conversion == null ? '' : r.sample_conversion.toFixed(2),
    'Muestras sugeridas': r.suggested_samples,
    'Presupuesto sugerido': r.suggested_budget_usd.toFixed(2),
  }))

  const loadingText = data.loading ? 'Cargando…' : null
  const empty = <EmptyChart>{loadingText ?? 'Ningún creador recibió muestras en este período.'}</EmptyChart>
  const noData = !data.loading && !data.error && rows.length === 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Política de muestras</h1>
          <p className="text-muted-foreground text-sm">{description}</p>
        </div>
        {onNavigate && (
          <Button variant="outline" onClick={() => onNavigate('creatorProfit')}>
            Ver rentabilidad por creador
          </Button>
        )}
      </div>
      <ErrorAlert message={data.error} />

      <Card>
        <CardContent className="flex flex-wrap items-end gap-4 pt-4">
          <NumberField
            label="Tope de muestras sin venta"
            value={maxSamples}
            min={LIMITS.maxSamples.min}
            max={LIMITS.maxSamples.max}
            onCommit={setMaxSamples}
            suffix="muestras"
          />
          <NumberField
            label="Días máximos a la primera venta"
            value={maxDays}
            min={LIMITS.maxDays.min}
            max={LIMITS.maxDays.max}
            onCommit={setMaxDays}
            suffix="días"
          />
          <Button
            variant="outline"
            disabled={isDefault}
            onClick={() => {
              setMaxSamples(LIMITS.maxSamples.default)
              setMaxDays(LIMITS.maxDays.default)
            }}
          >
            Restablecer
          </Button>
          <span className="text-muted-foreground text-xs sm:ml-auto">
            {data.loading ? 'Recalculando…' : 'Se aplica al salir del campo o con Enter.'}
          </span>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Creadores con muestra" value={count(s?.creators)} hint={s ? `${money(s.sample_cost)} en muestras · ${count(s.never_sold_creators)} nunca vendieron` : undefined} />
        <Kpi
          title="A pausar o cortar"
          value={count(s?.to_pause)}
          className={s && s.to_pause > 0 ? 'text-red-600' : ''}
          hint={s ? `${s.recommendation_counts.Pausar} pausar · ${s.recommendation_counts.Cortar} cortar` : undefined}
        />
        <Kpi
          title="Ahorro estimado"
          value={money(s?.estimated_savings)}
          className="text-green-600"
          hint={s ? `Neto ${money(s.net_benefit)} tras la utilidad en riesgo` : undefined}
        />
        <Kpi
          title="Falsos positivos"
          value={count(s?.false_positives)}
          className={s && s.false_positives > 0 ? 'text-amber-600' : ''}
          hint={s ? `${money(s.lost_sales)} en ventas en riesgo (cota superior)` : undefined}
        />
      </div>

      {s && (
        <Callout tone="info">
          Utilidad de afiliados: {money(s.profit_before_samples)} antes de muestras y{' '}
          <strong className={tone(s.profit_after_samples)}>{money(s.profit_after_samples)}</strong> después. Con esta regla habría quedado en{' '}
          <strong className={tone(s.profit_with_policy)}>{money(s.profit_with_policy)}</strong>.
        </Callout>
      )}
      {noData && <Callout tone="info">No hay muestras enviadas a creadores en este período.</Callout>}
      {d?.notes.map((n) => (
        <Callout key={n} tone="info">
          {n}
        </Callout>
      ))}

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Ahorro vs tope de muestras"
          subtitle={`Qué se habría ahorrado con cada tope (plazo fijo de ${maxDays} días). Clic en un tope para aplicarlo.`}
          filename="muestras-curva-ahorro"
          csv={curveData.map((p) => ({ Tope: p.cap, Ahorro: p.savings, 'Utilidad en riesgo': p.lost_profit, 'Falsos positivos': p.false_positives, Neto: p.net_benefit }))}
        >
          {curveData.length === 0 ? (
            empty
          ) : (
            <ComboChart
              data={curveData}
              xKey="cap_label"
              series={curveSeries}
              xFormat={(v) => `${v} mues.`}
              rightFormat={(v) => count(v)}
              onPointClick={(row) => {
                const cap = Number(row.cap)
                if (Number.isInteger(cap) && cap >= LIMITS.maxSamples.min && cap <= LIMITS.maxSamples.max) setMaxSamples(cap)
              }}
            />
          )}
        </ChartCard>

        <ChartCard
          title="Distribución de puntuaciones"
          subtitle={s ? `Seguir enviando desde ${d?.thresholds.send_score}, con tope desde ${d?.thresholds.cap_score}.` : undefined}
          filename="muestras-histograma"
          csv={histData.map((b) => ({ Rango: b.range, Creadores: b.count }))}
        >
          {histData.length === 0 || noData ? (
            empty
          ) : (
            <ComboChart
              data={histData}
              xKey="range"
              series={[{ key: 'count', label: 'Creadores', type: 'bar', color: COLOR.blue, format: (v) => count(v) }]}
              leftFormat={(v) => count(v)}
            />
          )}
        </ChartCard>
      </div>

      <ChartCard
        title="Costo de muestras vs utilidad"
        subtitle="Tamaño = ventas, color = recomendación. Sobre la diagonal el creador ya pagó sus muestras (utilidad antes de muestras). Clic para ver su desglose."
        filename="muestras-burbujas"
        csv={bubble.map((b) => ({ Creador: b.label, Muestras: b.x, 'Utilidad antes de muestras': b.y, Ventas: b.z, Recomendación: b.group }))}
      >
        {bubble.length === 0 ? (
          empty
        ) : (
          <BreakEvenBubble
            points={bubble}
            groups={BUBBLE_GROUPS}
            xLabel="Costo de muestras"
            yLabel="Utilidad antes de muestras"
            zLabel="Ventas"
            format={axisMoney}
            onPointClick={(p) => setSelected(p.id)}
          />
        )}
      </ChartCard>

      {chosen && <ScoreBreakdown row={chosen} />}

      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex flex-wrap items-center gap-3">
            <Input className="h-9 w-full sm:w-64" placeholder="Buscar creador o contenido" value={text} onChange={(e) => setText(e.target.value)} aria-label="Buscar creador" />
            <Segmented<Filter>
              label="Recomendación"
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'all', label: 'Todos', count: rows.length },
                ...RECOMMENDATIONS.map((r) => ({ value: r, label: r, count: s?.recommendation_counts[r] ?? 0 })),
              ]}
            />
            <span className="text-muted-foreground text-xs">
              {count(filtered.length)} de {count(rows.length)}
            </span>
            <div className="sm:ml-auto">
              <Button variant="outline" disabled={csv.length === 0} onClick={() => downloadCsv('politica-muestras.csv', csv)}>
                Descargar CSV
              </Button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {th('creator', 'Creador', false)}
                  {th('samples', 'Muestras')}
                  {th('sample_cost', 'Costo')}
                  {th('orders', 'Órdenes')}
                  {th('sales', 'Ventas')}
                  {th('profit_before_samples', 'Utilidad antes')}
                  {th('profit', 'Utilidad después')}
                  {th('days_to_first_sale', '1.ª venta')}
                  <TableHead>Contenido</TableHead>
                  {th('score', 'Puntuación')}
                  {th('recommendation', 'Recomendación', false)}
                  <TableHead className="text-right">Presupuesto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={12} className="text-muted-foreground py-10 text-center">
                      {loadingText ?? 'Sin resultados.'}
                    </TableCell>
                  </TableRow>
                )}
                {filtered.slice(0, TABLE_ROWS_SHOWN).map((r) => (
                  <TableRow key={r.creator} onClick={() => setSelected(r.creator)} className={`cursor-pointer ${selected === r.creator ? 'bg-slate-100' : ''}`}>
                    <TableCell className="max-w-[14rem]">
                      <div className="truncate font-medium" title={r.reason}>
                        {r.creator}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">{count(r.samples)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{money(r.sample_cost)}</TableCell>
                    <TableCell className="text-right">{count(r.orders)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{money(r.sales)}</TableCell>
                    <TableCell className={`text-right whitespace-nowrap ${tone(r.profit_before_samples)}`}>{money(r.profit_before_samples)}</TableCell>
                    <TableCell className={`text-right font-medium whitespace-nowrap ${tone(r.profit)}`}>{money(r.profit)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{r.days_to_first_sale == null ? 'sin venta' : `${r.days_to_first_sale} d`}</TableCell>
                    <TableCell>{r.content_type ?? '—'}</TableCell>
                    <TableCell>
                      <ScoreBar row={r} />
                    </TableCell>
                    <TableCell>
                      <Pill label={r.recommendation} className={REC_PILL[r.recommendation]} />
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap">
                      {r.suggested_samples > 0 ? `${r.suggested_samples} · ${money(r.suggested_budget_usd)}` : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {filtered.length > TABLE_ROWS_SHOWN && (
            <p className="text-muted-foreground text-xs">Se muestran {TABLE_ROWS_SHOWN}; el CSV incluye todas las filas filtradas.</p>
          )}
        </CardContent>
      </Card>

      {d && (
        <Hint>
          Puntuación 0-100: conversión {d.weights.conversion}, utilidad {d.weights.profit}, rapidez {d.weights.speed} y constancia {d.weights.consistency} puntos
          (pasa el cursor por la barra o toca una fila para ver el desglose). La simulación re-juega el historial: una muestra se bloquea si el creador aún no
          había vendido y ya recibió {d.params.max_samples_without_sale} muestras o pasaron {d.params.max_days_to_first_sale} días desde la primera. Un falso positivo es un
          creador que vendió después de la primera muestra bloqueada.
        </Hint>
      )}
    </div>
  )
}
