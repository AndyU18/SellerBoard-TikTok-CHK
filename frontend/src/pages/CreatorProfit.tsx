import { useMemo, useState } from 'react'

import { BreakEvenBubble, type BreakEvenPoint } from '@/components/analytics/BreakEvenBubble'
import { HBarChart } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, Pill, pct1, Segmented } from '@/components/analytics/ui'
import { HeatTable } from '@/components/charts/HeatTable'
import { axisMoney, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { Waterfall, type WaterfallStep } from '@/components/charts/Waterfall'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { creatorProfit, LEVEL_LABEL, type CreatorProfitRow, type ProfitLevel, type Verdict } from '@/lib/creatorProfit'
import { count, money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const VERDICT_COLOR: Record<Verdict, string> = {
  Renovar: COLOR.good,
  Mantener: COLOR.blue,
  Vigilar: COLOR.amber,
  Cortar: COLOR.bad,
}
const VERDICT_PILL: Record<Verdict, string> = {
  Renovar: 'bg-green-100 text-green-800',
  Mantener: 'bg-blue-100 text-blue-800',
  Vigilar: 'bg-amber-100 text-amber-800',
  Cortar: 'bg-red-100 text-red-800',
}
const VERDICTS = Object.keys(VERDICT_COLOR) as Verdict[]
const LEVEL_OPTIONS = (Object.keys(LEVEL_LABEL) as ProfitLevel[]).map((value) => ({ value, label: LEVEL_LABEL[value] }))
const WEEKS_SHOWN = 12
const SHORT = (s: string, n = 28) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

type SortKey = 'label' | 'orders' | 'sales' | 'commission' | 'sample_cost' | 'cogs' | 'profit' | 'margin' | 'roi' | 'payback_orders'

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

function waterfallSteps(r: CreatorProfitRow): WaterfallStep[] {
  const neg = (id: string, label: string, v: number): WaterfallStep => ({ id, label, kind: 'delta', value: -v })
  return [
    { id: 'sales', label: 'Ventas', kind: 'total', value: r.sales },
    neg('promo', 'Promo', r.promo),
    neg('platform_fee', 'Plataforma', r.platform_fee),
    neg('commission', 'Afiliado', r.commission),
    neg('shipping', 'Envío', r.shipping),
    neg('refunds', 'Reembolsos', r.refunds),
    neg('cogs', 'Producto', r.cogs),
    neg('sample_cost', 'Muestras', r.sample_cost),
    { id: 'profit', label: 'Utilidad', kind: 'total', value: r.profit },
  ]
}

export function CreatorProfit() {
  const [level, setLevel] = useState<ProfitLevel>('creator')
  const [text, setText] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [step, setStep] = useState<WaterfallStep | null>(null)
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'profit', dir: -1 })
  const { range } = useScope()
  const description = useScopeDescription()
  const start = range.start
  const end = range.end

  const data = useAsync(() => creatorProfit.report({ level, start, end, limit: 1000 }), [level, start, end])
  const d = data.data
  const rows = useMemo(() => d?.rows ?? [], [d])
  const label = LEVEL_LABEL[level]

  const chosen = rows.find((r) => r.key === selected) ?? rows[0] ?? null

  const filtered = useMemo(() => {
    const needle = text.trim().toLowerCase()
    const list = needle ? rows.filter((r) => `${r.label} ${r.key} ${r.creator ?? ''}`.toLowerCase().includes(needle)) : rows
    const val = (r: CreatorProfitRow) => (sort.key === 'label' ? r.label.toLowerCase() : (r[sort.key] ?? -Infinity))
    return [...list].sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * sort.dir)
  }, [rows, text, sort])

  const bubble: BreakEvenPoint[] = useMemo(
    () =>
      level !== 'creator'
        ? []
        : rows
            .filter((r) => r.sample_cost > 0)
            .map((r) => ({
              id: r.key,
              label: r.label,
              x: r.sample_cost,
              y: Math.max(0, r.profit_before_samples),
              z: Math.round(r.sales),
              group: r.verdict,
              extra: [
                { label: 'Utilidad real', value: money(r.profit) },
                { label: 'Veredicto', value: r.verdict },
              ],
            })),
    [rows, level],
  )

  const top = useMemo(() => rows.filter((r) => r.profit > 0).slice(0, 10), [rows])
  const worst = useMemo(() => [...rows].filter((r) => r.profit < 0).sort((a, b) => a.profit - b.profit).slice(0, 10), [rows])
  const hbar = (list: CreatorProfitRow[]) => list.map((r) => ({ id: r.key, label: SHORT(r.label), profit: r.profit }))

  const heatWeeks = useMemo(() => (d?.weeks ?? []).slice(-WEEKS_SHOWN), [d])
  const heatRows = useMemo(
    () =>
      [...rows]
        .sort((a, b) => b.sales - a.sales)
        .slice(0, 15)
        .map((r) => {
          const bySales = new Map(r.series.map((s) => [s.week, s.sales]))
          return { id: r.key, label: r.label, sublabel: r.creator && level !== 'creator' ? `@${r.creator}` : undefined, values: heatWeeks.map((w) => bySales.get(w) ?? 0) }
        }),
    [rows, heatWeeks, level],
  )

  const s = d?.summary
  const th = (key: SortKey, text: string, right = true) => (
    <TableHead className={right ? 'text-right' : ''}>
      <button type="button" className="hover:underline" onClick={() => setSort((p) => ({ key, dir: p.key === key ? (p.dir === 1 ? -1 : 1) : -1 }))}>
        {text}
        {sort.key === key ? (sort.dir === -1 ? ' ↓' : ' ↑') : ''}
      </button>
    </TableHead>
  )

  const csv = filtered.map((r) => ({
    [label]: r.label,
    Creador: r.creator ?? '',
    Órdenes: r.orders,
    Unidades: r.units,
    Ventas: r.sales.toFixed(2),
    Promo: r.promo.toFixed(2),
    'Comisión plataforma': r.platform_fee.toFixed(2),
    'Comisión afiliado': r.commission.toFixed(2),
    Envío: r.shipping.toFixed(2),
    Reembolsos: r.refunds.toFixed(2),
    Producto: r.cogs.toFixed(2),
    Muestras: r.sample_cost.toFixed(2),
    Utilidad: r.profit.toFixed(2),
    Margen: r.margin == null ? '' : r.margin.toFixed(4),
    ROI: r.roi == null ? '' : r.roi.toFixed(2),
    'Órdenes para pagar la muestra': r.payback_orders ?? '',
    Veredicto: r.verdict,
  }))

  const empty = <EmptyChart>{data.loading ? 'Cargando…' : `No hay ${label.toLowerCase()}s con ventas de afiliados en este período.`}</EmptyChart>

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Rentabilidad real por {label.toLowerCase()}</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
      </div>
      <ErrorAlert message={data.error} />

      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Nivel"
          value={level}
          options={LEVEL_OPTIONS}
          onChange={(v) => {
            setLevel(v)
            setSelected(null)
            setStep(null)
          }}
        />
        {data.loading && <span className="text-muted-foreground text-sm">Cargando…</span>}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Utilidad de afiliados"
          value={money(s?.affiliate_profit)}
          className={tone(s?.affiliate_profit)}
          hint={s ? `Después de ${money(s.sample_cost)} en muestras` : undefined}
        />
        <Kpi title="% de ventas por afiliados" value={pct1(s?.affiliate_share_of_sales)} hint={s ? `${money(s.affiliate_sales)} de ${money(s.total_sales)}` : undefined} />
        <Kpi
          title="Muestras sin retorno"
          value={money(s?.never_sold_cost)}
          className={s && s.never_sold_cost > 0 ? 'text-red-600' : ''}
          hint={s ? `${count(s.never_sold_creators)} creadores nunca vendieron` : undefined}
        />
        <Kpi
          title="A renovar vs cortar"
          value={s ? `${s.verdict_counts.Renovar} / ${s.verdict_counts.Cortar}` : '—'}
          hint={s ? `${LEVEL_LABEL[level]}s · ${s.verdict_counts.Mantener} mantener, ${s.verdict_counts.Vigilar} vigilar` : undefined}
        />
      </div>

      {s && s.affiliate_orders_pending > 0 && (
        <Callout tone="info">
          {count(s.affiliate_orders_pending)} órdenes de afiliado aún no están en ventas: cuentan como órdenes pero no suman dinero.
        </Callout>
      )}
      {d?.notes.map((n) => (
        <Callout key={n} tone="info">
          {n}
        </Callout>
      ))}

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={chosen ? `De ventas a utilidad: ${SHORT(chosen.label, 40)}` : 'De ventas a utilidad'}
          subtitle={`Elige ${label.toLowerCase()} en la tabla o en las barras. Clic en una barra para ver su peso sobre las ventas.`}
          filename="rentabilidad-cascada"
          csv={chosen ? waterfallSteps(chosen).map((x) => ({ Concepto: x.label, Monto: x.value.toFixed(2) })) : undefined}
        >
          {chosen ? (
            <>
              <Waterfall steps={waterfallSteps(chosen)} onStepClick={setStep} />
              {step && (
                <p className="text-muted-foreground mt-2 text-sm">
                  <strong>{step.label}</strong>: {money(step.value)}
                  {chosen.sales > 0 && ` (${((Math.abs(step.value) / chosen.sales) * 100).toFixed(1)}% de las ventas)`}
                </p>
              )}
            </>
          ) : (
            empty
          )}
        </ChartCard>

        <ChartCard
          title="Costo de muestras vs utilidad"
          subtitle="Tamaño = ventas, color = veredicto. Sobre la diagonal el creador ya pagó su muestra (utilidad antes de muestras)."
          filename="rentabilidad-burbujas"
          csv={bubble.map((b) => ({ Creador: b.label, Muestras: b.x, 'Utilidad antes de muestras': b.y, Ventas: b.z, Veredicto: b.group }))}
        >
          {level !== 'creator' ? (
            <EmptyChart>Las muestras solo se pueden ligar a creadores. Cambia a «Creador».</EmptyChart>
          ) : bubble.length === 0 ? (
            empty
          ) : (
            <BreakEvenBubble
              points={bubble}
              groups={Object.fromEntries(VERDICTS.map((v) => [v, { label: v, color: VERDICT_COLOR[v] }]))}
              xLabel="Costo de muestras"
              yLabel="Utilidad antes de muestras"
              zLabel="Ventas"
              format={axisMoney}
              onPointClick={(p) => setSelected(p.id)}
            />
          )}
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard title={`Top 10 por utilidad`} subtitle="Clic en una barra para ver su cascada." filename="rentabilidad-top-utilidad" csv={top.map((r) => ({ [label]: r.label, Utilidad: r.profit.toFixed(2) }))}>
          {top.length === 0 ? empty : <HBarChart rows={hbar(top)} series={[{ key: 'profit', label: 'Utilidad', color: COLOR.good }]} format={axisMoney} selectedId={chosen?.key} onRowClick={(r) => setSelected(r.id)} labelWidth={150} />}
        </ChartCard>
        <ChartCard title="Top 10 por pérdida" subtitle="Lo que más resta después de todos los costos." filename="rentabilidad-top-perdida" csv={worst.map((r) => ({ [label]: r.label, Utilidad: r.profit.toFixed(2) }))}>
          {worst.length === 0 ? (
            <EmptyChart>{data.loading ? 'Cargando…' : 'Nada pierde dinero en este período.'}</EmptyChart>
          ) : (
            <HBarChart rows={hbar(worst)} series={[{ key: 'profit', label: 'Utilidad', color: COLOR.bad }]} format={axisMoney} selectedId={chosen?.key} onRowClick={(r) => setSelected(r.id)} labelWidth={150} />
          )}
        </ChartCard>
      </div>

      <ChartCard title="Ventas por semana: top 15" subtitle={`Las ${WEEKS_SHOWN} semanas más recientes con datos (semana que empieza el lunes). Clic en una fila para ver su cascada.`} filename="rentabilidad-semanas">
        {heatRows.length === 0 ? (
          empty
        ) : (
          <HeatTable columns={heatWeeks.map((w) => w.slice(5))} rows={heatRows} format={money} firstColumn={label} showChange={false} onRowClick={(r) => setSelected(r.id)} maxRows={15} />
        )}
      </ChartCard>

      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex flex-wrap items-center gap-3">
            <Input className="h-9 w-64" placeholder={`Buscar ${label.toLowerCase()} o creador`} value={text} onChange={(e) => setText(e.target.value)} />
            <span className="text-muted-foreground text-xs">{count(filtered.length)} de {count(rows.length)}</span>
            <div className="sm:ml-auto">
              <Button variant="outline" disabled={csv.length === 0} onClick={() => downloadCsv(`rentabilidad-${level}.csv`, csv)}>
                Descargar CSV
              </Button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {th('label', label, false)}
                  {th('orders', 'Órdenes')}
                  {th('sales', 'Ventas')}
                  {th('commission', 'Afiliado')}
                  {th('cogs', 'Producto')}
                  {th('sample_cost', 'Muestras')}
                  {th('profit', 'Utilidad')}
                  {th('margin', 'Margen')}
                  {th('roi', 'ROI muestra')}
                  {th('payback_orders', 'Pago en')}
                  <TableHead>Veredicto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={11} className="text-muted-foreground py-10 text-center">
                      {data.loading ? 'Cargando…' : 'Sin resultados.'}
                    </TableCell>
                  </TableRow>
                )}
                {filtered.slice(0, 200).map((r) => (
                  <TableRow key={r.key} onClick={() => setSelected(r.key)} className={`cursor-pointer ${chosen?.key === r.key ? 'bg-slate-100' : ''}`}>
                    <TableCell className="max-w-[16rem]">
                      <div className="truncate font-medium" title={r.label}>{r.label}</div>
                      {r.creator && level !== 'creator' && <div className="text-muted-foreground text-xs">@{r.creator}</div>}
                    </TableCell>
                    <TableCell className="text-right">{count(r.orders)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{money(r.sales)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{money(r.commission)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{money(r.cogs)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">{r.sample_cost ? money(r.sample_cost) : '—'}</TableCell>
                    <TableCell className={`text-right font-medium whitespace-nowrap ${tone(r.profit)}`}>{money(r.profit)}</TableCell>
                    <TableCell className="text-right">{pct1(r.margin)}</TableCell>
                    <TableCell className="text-right">{r.roi == null ? '—' : `${r.roi.toFixed(1)}x`}</TableCell>
                    <TableCell className="text-right">{r.payback_orders == null ? (r.sample_cost > 0 ? 'nunca' : '—') : `${r.payback_orders} órd.`}</TableCell>
                    <TableCell>
                      <Pill label={r.verdict} className={VERDICT_PILL[r.verdict]} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {filtered.length > 200 && <p className="text-muted-foreground text-xs">Se muestran 200; el CSV incluye todas las filas filtradas.</p>}
        </CardContent>
      </Card>

      {d && (
        <Hint>
          Veredicto: con menos de {d.thresholds.min_orders} órdenes se vigila; con pérdida se corta; margen de {pct1(d.thresholds.renew_margin)} o más se
          renueva y de {pct1(d.thresholds.keep_margin)} o más se mantiene. Sin ventas, la muestra se corta tras {d.thresholds.sample_grace_days} días.
          La comisión del afiliado se separa de la comisión de la plataforma para no contarla dos veces.
        </Hint>
      )}
    </div>
  )
}
