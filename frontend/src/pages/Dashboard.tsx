import { useMemo, useState, type ReactNode } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ErrorAlert } from '@/components/analytics/ui'
import { ChannelBadge } from '@/components/ChannelBadge'
import { ChartCard, EmptyChart, type CsvRow } from '@/components/charts/kit'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { HeatTable, type HeatRow } from '@/components/charts/HeatTable'
import { SparkKpi } from '@/components/charts/SparkKpi'
import { Waterfall, type WaterfallStep } from '@/components/charts/Waterfall'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { api, CHANNEL_COLOR, CHANNEL_LABEL, CHANNELS, type Filters, type ProfitRow } from '@/lib/api'
import { chartdata } from '@/lib/chartdata'
import { change, count, money, neg, percent, previousRange, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

function Stat({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className={`text-sm font-medium ${className}`}>{value}</div>
    </div>
  )
}

function ProductList({ title, rows, empty }: { title: string; rows: ProfitRow[]; empty: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">{empty}</p>
        ) : (
          <ul className="divide-y text-sm">
            {rows.map((r) => (
              <li key={r.sku} className="flex items-center justify-between gap-3 py-1.5">
                <span className="min-w-0 truncate" title={r.product_name ?? r.sku}>
                  <span className="font-medium">{r.sku}</span>
                  <span className="text-muted-foreground ml-2 text-xs">{count(r.units)} unid.</span>
                </span>
                <span className={`shrink-0 font-medium ${tone(r.net_profit)}`}>{money(r.net_profit)}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function SummaryCard({ title, row }: { title: ReactNode; row: ProfitRow }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className={`text-2xl font-semibold ${tone(row.net_profit)}`}>{money(row.net_profit)}</div>
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Unidades" value={count(row.units)} />
          <Stat label="Utilidad / unid." value={money(row.unit_profit)} className={tone(row.unit_profit)} />
          <Stat label="Margen" value={percent(row.margin)} className={tone(row.margin)} />
        </div>
      </CardContent>
    </Card>
  )
}

type MoneyKey = 'gross_sales' | 'promo' | 'platform_fees' | 'shipping_cost' | 'refund_amount' | 'ad_spend' | 'cogs' | 'allocated_expenses' | 'net_profit'

/** Conceptos de la cascada: cómo se llaman y de qué columna salen. */
const CONCEPTS: { key: MoneyKey; label: string; cost: boolean }[] = [
  { key: 'gross_sales', label: 'Ventas', cost: false },
  { key: 'promo', label: 'Promociones', cost: true },
  { key: 'platform_fees', label: 'Comisiones', cost: true },
  { key: 'shipping_cost', label: 'Envío', cost: true },
  { key: 'refund_amount', label: 'Reembolsos', cost: true },
  { key: 'ad_spend', label: 'Publicidad', cost: true },
  { key: 'cogs', label: 'Costo del producto', cost: true },
  { key: 'allocated_expenses', label: 'Gastos asignados', cost: true },
  { key: 'net_profit', label: 'Utilidad neta', cost: false },
]

function sum(rows: ProfitRow[], key: MoneyKey | 'units'): number {
  return rows.reduce((s, r) => s + (r[key] ?? 0), 0)
}

const HEAT_METRICS = [
  { key: 'gross_sales', label: 'Ventas', fmt: money },
  { key: 'units', label: 'Unidades', fmt: count },
  { key: 'ad_spend', label: 'Publicidad', fmt: money },
  { key: 'refund_amount', label: 'Reembolsos', fmt: money },
  { key: 'net_profit', label: 'Utilidad', fmt: money },
] as const
type HeatKey = (typeof HEAT_METRICS)[number]['key']

const COMBO: ComboSeries[] = [
  { key: 'net_profit', label: 'Utilidad neta', type: 'bar', color: '#10b981', format: money },
  { key: 'ad_spend', label: 'Publicidad', type: 'bar', color: '#f59e0b', format: money },
  { key: 'refund_amount', label: 'Reembolsos', type: 'bar', color: '#ef4444', format: money },
  { key: 'gross_sales', label: 'Ventas', type: 'line', color: '#3b82f6', format: money },
  { key: 'units', label: 'Unidades (eje derecho)', type: 'line', axis: 'right', color: '#8b5cf6', format: count },
]

export function Dashboard() {
  const scope = useScope()
  const { preset, range } = scope
  const [pick, setPick] = useState<string | null>(null)
  const [concept, setConcept] = useState<MoneyKey | null>(null)
  const [heat, setHeat] = useState<HeatKey>('gross_sales')

  const filters = useMemo<Filters>(
    () => ({ ...range, channel: scope.channelParam, account: scope.account }),
    [range, scope.channelParam, scope.account],
  )
  const interval = preset === '30d' ? 'day' : 'month'

  const byChannel = useAsync(() => api.profit('channel', filters), [filters])
  const series = useAsync(() => api.timeseries(interval, filters), [filters, interval])
  const byProduct = useAsync(() => api.profit('product', filters), [filters])
  const monthly = useAsync(() => chartdata.productMonthly(filters), [filters])
  const previous = useAsync(async () => {
    const prev = previousRange(preset)
    return prev ? api.profit('channel', { ...prev, channel: filters.channel, account: filters.account }) : null
  }, [preset, filters.channel, filters.account])

  const productRows = byProduct.data?.rows ?? []
  const topProducts = [...productRows].sort((a, b) => b.net_profit - a.net_profit).slice(0, 5)
  const lossProducts = productRows.filter((r) => r.net_profit < 0).sort((a, b) => a.net_profit - b.net_profit).slice(0, 5)
  const noCost = productRows.filter((r) => r.cogs === 0 && r.units > 0)
  const noCostSales = noCost.reduce((s, r) => s + r.gross_sales, 0)
  const rows = useMemo(() => byChannel.data?.rows ?? [], [byChannel.data])
  const totals = byChannel.data?.totals ?? null
  const prevTotals = previous.data?.totals ?? null

  const seriesRows = useMemo(() => series.data?.rows ?? [], [series.data])
  const fmtPeriod = (p: string | number) => String(p).slice(interval === 'day' ? 5 : 0, interval === 'day' ? 10 : 7)

  // Una fila por período con todo sumado (todos los canales) para el combo y los mini-gráficos.
  const combo = useMemo(() => {
    const by = new Map<string, Record<string, string | number>>()
    for (const r of seriesRows) {
      if (!r.period) continue
      const e = by.get(r.period) ?? { period: r.period, units: 0, gross_sales: 0, ad_spend: 0, refund_amount: 0, net_profit: 0 }
      for (const k of ['units', 'gross_sales', 'ad_spend', 'refund_amount', 'net_profit'] as const) e[k] = Number(e[k]) + r[k]
      by.set(r.period, e)
    }
    return [...by.values()].sort((a, b) => String(a.period).localeCompare(String(b.period)))
  }, [seriesRows])
  const sparkOf = (k: string) => combo.map((r) => Number(r[k]))

  const channelChart = useMemo(() => {
    const by = new Map<string, Record<string, number | string>>()
    for (const r of seriesRows) {
      if (!r.period || !r.channel) continue
      const e = by.get(r.period) ?? { period: r.period }
      e[r.channel] = r.net_profit
      by.set(r.period, e)
    }
    return [...by.values()].sort((a, b) => String(a.period).localeCompare(String(b.period)))
  }, [seriesRows])

  // Período elegido con un clic en el combo: la cascada y el detalle pasan a ese período.
  const active = pick && combo.some((r) => r.period === pick) ? pick : null
  const scopedRows = useMemo(
    () => (active ? seriesRows.filter((r) => r.period === active) : rows),
    [active, seriesRows, rows],
  )
  const scopedTotals = useMemo(() => {
    if (!active) return totals
    if (!scopedRows.length) return null
    const t = { ...scopedRows[0] } as ProfitRow
    for (const c of CONCEPTS) t[c.key] = sum(scopedRows, c.key)
    t.units = sum(scopedRows, 'units')
    return t
  }, [active, scopedRows, totals])

  const steps = useMemo<WaterfallStep[]>(() => {
    if (!scopedTotals) return []
    return CONCEPTS.filter((c) => c.key !== 'allocated_expenses' || scopedTotals.allocated_expenses !== 0).map((c) => ({
      id: c.key,
      label: c.label,
      kind: c.cost ? 'delta' : 'total',
      value: c.cost ? neg(scopedTotals[c.key]) : scopedTotals[c.key],
    }))
  }, [scopedTotals])

  const conceptInfo = CONCEPTS.find((c) => c.key === concept)
  const sales = scopedTotals?.gross_sales ?? 0

  const heatRows = useMemo<HeatRow[]>(() => {
    const m = monthly.data
    if (!m) return []
    const by = new Map<string, HeatRow>()
    for (const r of m.rows) {
      const row = by.get(r.sku) ?? { id: r.sku, label: r.product_name ?? r.sku, sublabel: r.sku, values: m.months.map(() => 0) }
      row.values[m.months.indexOf(r.period)] = r[heat]
      by.set(r.sku, row)
    }
    return [...by.values()]
  }, [monthly.data, heat])
  const heatMonths = (monthly.data?.months ?? []).map((m) => m.slice(0, 7))
  const heatMetric = HEAT_METRICS.find((m) => m.key === heat)!

  const error = byChannel.error ?? series.error
  const comboCsv: CsvRow[] = combo.map((r) => ({ periodo: String(r.period), ...Object.fromEntries(COMBO.map((s) => [s.label, r[s.key]])) }))
  const channelCsv: CsvRow[] = scopedRows.map((r) => ({ canal: r.channel, periodo: r.period ?? 'total', ...Object.fromEntries(CONCEPTS.map((c) => [c.label, r[c.key]])) }))
  const heatCsv: CsvRow[] = heatRows.map((r) => ({ sku: r.id, producto: r.label, ...Object.fromEntries(heatMonths.map((m, i) => [m, r.values[i]])) }))
  const kpiDelta = (key: MoneyKey | 'units') => (totals && prevTotals ? change(totals[key], prevTotals[key]) : null)

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Resumen ejecutivo</h1>

      <ErrorAlert message={error} />

      {noCost.length > 0 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>La utilidad está inflada:</strong> {noCost.length} productos vendidos no tienen costo (COGS), el{' '}
          {(totals?.gross_sales ?? 0) > 0 ? ((noCostSales / (totals?.gross_sales ?? 1)) * 100).toFixed(0) : 0}% de las ventas del
          periodo. Complétalos en la sección Costos (COGS).
        </div>
      )}

      {totals && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <SparkKpi title="Utilidad neta" value={money(totals.net_profit)} delta={kpiDelta('net_profit')} spark={sparkOf('net_profit')} />
          <SparkKpi title="Ventas" value={money(totals.gross_sales)} delta={kpiDelta('gross_sales')} spark={sparkOf('gross_sales')} />
          <SparkKpi title="Unidades" value={count(totals.units)} delta={kpiDelta('units')} spark={sparkOf('units')} />
          <SparkKpi
            title="Margen"
            value={percent(totals.margin)}
            delta={totals.margin != null && prevTotals?.margin != null ? change(totals.margin, prevTotals.margin) : null}
          />
          <SparkKpi title="Publicidad" value={money(totals.ad_spend)} delta={kpiDelta('ad_spend')} spark={sparkOf('ad_spend')} goodWhenUp={false} />
          <SparkKpi title="Reembolsos" value={money(totals.refund_amount)} delta={kpiDelta('refund_amount')} spark={sparkOf('refund_amount')} goodWhenUp={false} />
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {totals && <SummaryCard title="Total" row={totals} />}
        {CHANNELS.map((c) => {
          const row = rows.find((r) => r.channel === c)
          return row ? <SummaryCard key={c} title={<ChannelBadge channel={c} />} row={row} /> : null
        })}
      </div>

      <ChartCard
        title={`Evolución ${interval === 'day' ? 'diaria' : 'mensual'}`}
        subtitle="Barras: utilidad, publicidad y reembolsos. Líneas: ventas y unidades (eje derecho). Toca un período para ver su cascada abajo; toca la leyenda para ocultar series."
        csv={comboCsv}
        filename="resumen-evolucion"
        actions={
          active ? (
            <button type="button" onClick={() => setPick(null)} className="rounded border px-2 py-1 text-xs hover:bg-slate-100">
              Período: {fmtPeriod(active)} · quitar
            </button>
          ) : undefined
        }
      >
        {combo.length === 0 ? (
          <EmptyChart>{series.loading ? 'Cargando…' : 'Sin datos para este filtro.'}</EmptyChart>
        ) : (
          <ComboChart
            data={combo}
            xKey="period"
            series={COMBO}
            xFormat={fmtPeriod}
            rightFormat={(v) => count(v)}
            onPointClick={(r) => {
              setPick(String(r.period))
              setConcept(null)
            }}
          />
        )}
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <ChartCard
          title="De ventas a utilidad neta"
          subtitle={`${active ? `Período ${fmtPeriod(active)}` : 'Todo el período'} · toca una barra para ver el detalle por canal`}
          csv={steps.map((s) => ({ concepto: s.label, monto: s.value }))}
          filename="resumen-cascada"
        >
          {steps.length === 0 ? (
            <EmptyChart>{byChannel.loading ? 'Cargando…' : 'Sin datos para este filtro.'}</EmptyChart>
          ) : (
            <Waterfall steps={steps} onStepClick={(s) => setConcept(s.id as MoneyKey)} />
          )}
        </ChartCard>

        <ChartCard title={conceptInfo ? `Detalle: ${conceptInfo.label}` : 'Detalle del concepto'} csv={concept ? channelCsv : undefined} filename="resumen-detalle-concepto">
          {!conceptInfo || !scopedTotals ? (
            <EmptyChart>Toca una barra de la cascada.</EmptyChart>
          ) : (
            <div className="space-y-3 text-sm">
              <div>
                <div className="text-2xl font-semibold tabular-nums">{money(scopedTotals[conceptInfo.key])}</div>
                <div className="text-muted-foreground text-xs">
                  {sales > 0 ? `${percent(scopedTotals[conceptInfo.key] / sales)} de las ventas` : 'Sin ventas en el período'}
                </div>
              </div>
              <ul className="divide-y">
                {scopedRows.map((r, i) => (
                  <li key={`${r.channel}${i}`} className="flex items-center justify-between gap-2 py-1.5">
                    {r.channel ? <ChannelBadge channel={r.channel} /> : <span>Total</span>}
                    <span className="tabular-nums">
                      {money(r[conceptInfo.key])}
                      <span className="text-muted-foreground ml-2 text-xs">
                        {r.gross_sales > 0 ? percent(r[conceptInfo.key] / r.gross_sales) : '—'}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </ChartCard>
      </div>

      <ChartCard
        title="Producto × mes"
        subtitle="Mapa de calor por fila (rojo = mes más bajo del producto, verde = más alto) con la variación contra el mes anterior."
        csv={heatCsv}
        filename={`resumen-producto-mes-${heat}`}
        actions={
          <div className="flex flex-wrap gap-1" role="group" aria-label="Métrica">
            {HEAT_METRICS.map((m) => (
              <button
                key={m.key}
                type="button"
                aria-pressed={heat === m.key}
                onClick={() => setHeat(m.key)}
                className={`rounded border px-2 py-1 text-xs ${heat === m.key ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`}
              >
                {m.label}
              </button>
            ))}
          </div>
        }
      >
        <ErrorAlert message={monthly.error} />
        {monthly.loading && !monthly.data ? (
          <EmptyChart>Cargando…</EmptyChart>
        ) : heatRows.length === 0 ? (
          !monthly.error && <EmptyChart />
        ) : (
          <HeatTable columns={heatMonths} rows={heatRows} format={heatMetric.fmt} />
        )}
      </ChartCard>

      <div className="grid gap-4 md:grid-cols-2">
        <ProductList title="Top 5 productos por utilidad" rows={topProducts} empty="Sin ventas en este periodo." />
        <ProductList title="Productos con pérdida" rows={lossProducts} empty="Ningún producto pierde dinero en este periodo." />
      </div>

      <ChartCard
        title={`Utilidad neta por canal (${interval === 'day' ? 'diaria' : 'mensual'})`}
        csv={channelChart.map((r) => ({ ...r }) as CsvRow)}
        filename="resumen-utilidad-por-canal"
      >
        {channelChart.length === 0 ? (
          <EmptyChart>{series.loading ? 'Cargando…' : 'Sin datos para este filtro.'}</EmptyChart>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={channelChart}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tickFormatter={fmtPeriod} tick={{ fontSize: 11 }} />
              <YAxis tickFormatter={(v: number) => `$${v.toLocaleString('en-US')}`} width={72} tick={{ fontSize: 11 }} />
              <Tooltip formatter={(v) => money(Number(v))} />
              <Legend />
              <ReferenceLine y={0} stroke="#888" />
              {CHANNELS.map((c) => (
                <Bar key={c} dataKey={c} name={CHANNEL_LABEL[c]} stackId="profit" fill={CHANNEL_COLOR[c]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
    </div>
  )
}
