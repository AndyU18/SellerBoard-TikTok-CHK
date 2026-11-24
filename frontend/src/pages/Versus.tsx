import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ErrorAlert } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisMoney, axisNumber, ChartCard, EmptyChart, type CsvRow } from '@/components/charts/kit'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, CHANNEL_LABEL, type Channel, type Filters, type GroupBy, type ProfitRow } from '@/lib/api'
import { chartdata } from '@/lib/chartdata'
import { count, money, percent, previousRange, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type Mode = 'channel' | 'account' | 'product' | 'sku_channels' | 'period'

const MODES: { value: Mode; label: string }[] = [
  { value: 'channel', label: 'Canal vs canal' },
  { value: 'account', label: 'Tienda vs tienda' },
  { value: 'product', label: 'Producto vs producto' },
  { value: 'sku_channels', label: 'Mismo producto en dos canales' },
  { value: 'period', label: 'Periodo vs periodo anterior' },
]

interface Metric {
  key: keyof ProfitRow
  label: string
  fmt: (v: number | null) => string
  /** high = gana el mayor, low = gana el menor, null = no hay ganador (solo informativo). */
  better: 'high' | 'low' | null
}

const METRICS: Metric[] = [
  { key: 'gross_sales', label: 'Ventas', fmt: money, better: 'high' },
  { key: 'units', label: 'Unidades', fmt: count, better: 'high' },
  { key: 'promo', label: 'Promociones', fmt: money, better: null },
  { key: 'platform_fees', label: 'Comisiones', fmt: money, better: null },
  { key: 'shipping_cost', label: 'Envío', fmt: money, better: null },
  { key: 'ad_spend', label: 'Publicidad', fmt: money, better: null },
  { key: 'refund_amount', label: 'Reembolsos', fmt: money, better: 'low' },
  { key: 'cogs', label: 'Costo de productos', fmt: money, better: null },
  { key: 'net_profit', label: 'Utilidad neta', fmt: money, better: 'high' },
  { key: 'unit_profit', label: 'Utilidad por unidad', fmt: money, better: 'high' },
  { key: 'margin', label: 'Margen', fmt: percent, better: 'high' },
  { key: 'roi', label: 'ROI', fmt: percent, better: 'high' },
]

const TREND_METRICS = [
  { key: 'gross_sales', label: 'Ventas', fmt: money },
  { key: 'units', label: 'Unidades', fmt: count },
  { key: 'net_profit', label: 'Utilidad neta', fmt: money },
  { key: 'ad_spend', label: 'Publicidad', fmt: money },
  { key: 'refund_amount', label: 'Reembolsos', fmt: money },
] as const
type TrendKey = (typeof TREND_METRICS)[number]['key']

const A_COLOR = '#3b82f6'
const B_COLOR = '#f59e0b'

function num(row: ProfitRow | null | undefined, key: keyof ProfitRow): number | null {
  const v = row?.[key]
  return typeof v === 'number' ? v : null
}

/** Qué consulta pide cada modo (el periodo usa su propia consulta). */
const GROUP_BY: Record<Exclude<Mode, 'period'>, GroupBy> = {
  channel: 'channel',
  account: 'account',
  product: 'product',
  sku_channels: 'channel_product',
}

export function Versus() {
  const scope = useScope()
  const [mode, setMode] = useState<Mode>('channel')
  const { preset } = scope
  const [pickA, setPickA] = useState('')
  const [pickB, setPickB] = useState('')
  const [pickSku, setPickSku] = useState('')
  const [trendKey, setTrendKey] = useState<TrendKey>('gross_sales')

  const filters = useMemo<Filters>(
    () => ({ ...scope.range, channel: scope.channelParam, account: scope.account }),
    [scope.range, scope.channelParam, scope.account],
  )
  const prevRange = previousRange(preset)

  // Canal vs canal y mismo producto en dos canales miran todos los canales; tienda vs tienda respeta el canal elegido.
  const entityFilters = useMemo<Filters>(() => {
    if (mode === 'channel' || mode === 'sku_channels') return { ...filters, channel: '', account: '' }
    if (mode === 'account') return { ...filters, account: '' }
    return filters
  }, [mode, filters])

  const entities = useAsync(
    () => (mode === 'period' ? Promise.resolve(null) : api.profit(GROUP_BY[mode], entityFilters)),
    [mode, entityFilters],
  )
  const periodA = useAsync(
    () => (mode === 'period' ? api.profit('channel', filters) : Promise.resolve(null)),
    [mode, filters],
  )
  const periodB = useAsync(
    () =>
      mode === 'period' && prevRange
        ? api.profit('channel', { ...prevRange, channel: filters.channel, account: filters.account })
        : Promise.resolve(null),
    [mode, preset, filters.channel, filters.account],
  )

  // Series en el tiempo de las dos entidades (para superponer líneas). 30 días = por día; el resto, por mes.
  const interval = preset === '30d' ? 'day' : 'month'
  const trendEntities = useAsync(
    () => (mode === 'period' ? Promise.resolve(null) : chartdata.versusSeries(GROUP_BY[mode], interval, entityFilters)),
    [mode, interval, entityFilters],
  )
  const trendCur = useAsync(
    () => (mode === 'period' ? api.timeseries('day', filters) : Promise.resolve(null)),
    [mode, filters],
  )
  const trendPrev = useAsync(
    () =>
      mode === 'period' && prevRange
        ? api.timeseries('day', { ...prevRange, channel: filters.channel, account: filters.account })
        : Promise.resolve(null),
    [mode, preset, filters.channel, filters.account],
  )

  const allRows = useMemo(() => entities.data?.rows ?? [], [entities.data])

  // Mismo producto en dos canales: se elige un SKU y las opciones son los canales donde se vendió.
  const skuOptions = useMemo(() => {
    const totals = new Map<string, { label: string; sales: number; channels: number }>()
    for (const r of allRows) {
      if (!r.sku) continue
      const t = totals.get(r.sku) ?? { label: r.product_name ?? r.sku, sales: 0, channels: 0 }
      t.sales += r.gross_sales
      t.channels += 1
      totals.set(r.sku, t)
    }
    return [...totals.entries()]
      .sort((x, y) => y[1].channels - x[1].channels || y[1].sales - x[1].sales)
      .map(([skuId, t]) => ({
        value: skuId,
        label: `${t.label} (${t.channels} ${t.channels === 1 ? 'canal' : 'canales'})`,
      }))
  }, [allRows])
  const sku = skuOptions.some((o) => o.value === pickSku) ? pickSku : (skuOptions[0]?.value ?? '')

  const rows = useMemo(
    () => (mode === 'sku_channels' ? allRows.filter((r) => r.sku === sku) : allRows),
    [mode, allRows, sku],
  )

  const idOf = (r: ProfitRow) => {
    if (mode === 'channel' || mode === 'sku_channels') return r.channel ?? ''
    if (mode === 'account') return `${r.channel}|${r.account}`
    return r.sku ?? ''
  }
  const labelOf = (r: ProfitRow) => {
    if (mode === 'channel' || mode === 'sku_channels') return CHANNEL_LABEL[r.channel as Channel] ?? r.channel ?? ''
    if (mode === 'account') return `${CHANNEL_LABEL[r.channel as Channel] ?? r.channel} · ${r.account}`
    return r.product_name ?? r.sku ?? ''
  }

  const sorted = useMemo(() => [...rows].sort((x, y) => y.gross_sales - x.gross_sales), [rows])
  const options = sorted.map((r) => ({ value: idOf(r), label: labelOf(r) }))
  // Si no eligieron (o cambió el modo), se comparan los dos de mayores ventas.
  const a = options.some((o) => o.value === pickA) ? pickA : (options[0]?.value ?? '')
  const b =
    options.some((o) => o.value === pickB) && pickB !== a
      ? pickB
      : (options.find((o) => o.value !== a)?.value ?? '')

  const rowA = mode === 'period' ? periodA.data?.totals : sorted.find((r) => idOf(r) === a)
  const rowB = mode === 'period' ? periodB.data?.totals : sorted.find((r) => idOf(r) === b)
  const nameA = mode === 'period' ? 'Periodo actual' : (options.find((o) => o.value === a)?.label ?? 'A')
  const nameB = mode === 'period' ? 'Periodo anterior' : (options.find((o) => o.value === b)?.label ?? 'B')

  const loading = entities.loading || periodA.loading || periodB.loading
  const error = entities.error ?? periodA.error ?? periodB.error
  const trendError = trendEntities.error ?? trendCur.error ?? trendPrev.error
  const trendLoading = trendEntities.loading || trendCur.loading || trendPrev.loading
  const trendMetric = TREND_METRICS.find((m) => m.key === trendKey) ?? TREND_METRICS[0]

  type TrendRow = { x: string; A: number | null; B: number | null; diff: number | null }
  const trend: TrendRow[] = (() => {
    const k = trendKey
    const withDiff = (x: string, A: number | null, B: number | null): TrendRow => ({ x, A, B, diff: A != null && B != null ? A - B : null })
    if (mode === 'period') {
      // Se alinean por día del período (día 1 con día 1) para ver cómo va el actual contra el anterior.
      const byDay = (rows: ProfitRow[] | undefined, start: string | undefined) => {
        const out = new Map<number, number>()
        if (!start) return out
        const t0 = new Date(`${start}T00:00:00`).getTime()
        for (const r of rows ?? []) {
          if (!r.period) continue
          const i = Math.round((new Date(`${r.period.slice(0, 10)}T00:00:00`).getTime() - t0) / 86_400_000)
          out.set(i, (out.get(i) ?? 0) + r[k])
        }
        return out
      }
      const cur = byDay(trendCur.data?.rows, filters.start)
      const prev = byDay(trendPrev.data?.rows, prevRange?.start)
      const n = Math.max(-1, ...cur.keys(), ...prev.keys()) + 1
      return Array.from({ length: n }, (_, i) => withDiff(`Día ${i + 1}`, cur.get(i) ?? 0, prev.get(i) ?? 0))
    }
    const all = trendEntities.data?.rows ?? []
    const scoped = mode === 'sku_channels' ? all.filter((r) => r.sku === sku) : all
    const periods = [...new Set(scoped.map((r) => r.period ?? ''))].sort()
    const valueAt = (id: string, p: string) => scoped.filter((r) => r.period === p && idOf(r) === id).reduce((s, r) => s + r[k], 0)
    return periods.map((p) => withDiff(p.slice(interval === 'day' ? 5 : 0, interval === 'day' ? 10 : 7), valueAt(a, p), valueAt(b, p)))
  })()
  const trendSeries: ComboSeries[] = [
    { key: 'A', label: nameA, type: 'line', color: A_COLOR, format: trendMetric.fmt },
    { key: 'B', label: nameB, type: 'line', color: B_COLOR, format: trendMetric.fmt },
    { key: 'diff', label: 'Diferencia (A − B)', type: 'bar', color: '#94a3b8', format: trendMetric.fmt },
  ]
  const trendCsv: CsvRow[] = trend.map((r) => ({ periodo: r.x, [nameA]: r.A, [nameB]: r.B, diferencia: r.diff }))
  const ready = rowA && rowB
  const chartData = [
    { name: 'Ventas', key: 'gross_sales' as const },
    { name: 'Utilidad neta', key: 'net_profit' as const },
  ].map((m) => ({ name: m.name, A: num(rowA, m.key) ?? 0, B: num(rowB, m.key) ?? 0 }))

  const ignoresScope = mode === 'channel' || mode === 'sku_channels'
  const scopeName = scope.account || (scope.channel === 'all' ? 'todos los canales' : CHANNEL_LABEL[scope.channel])

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Comparativa head-to-head</h1>

      <div className="flex flex-wrap items-center gap-4">
        <Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
          <TabsList>
            {MODES.map((m) => (
              <TabsTrigger key={m.value} value={m.value}>
                {m.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        {mode === 'sku_channels' && skuOptions.length > 0 && (
          <Select items={skuOptions} value={sku} onValueChange={(v) => v && setPickSku(v)}>
            <SelectTrigger className="w-80" aria-label="Producto">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {skuOptions.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {mode !== 'period' && options.length > 0 && (
          <div className="flex items-center gap-2">
            <Select items={options} value={a} onValueChange={(v) => v && setPickA(v)}>
              <SelectTrigger className="w-56" aria-label="Opción A">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-muted-foreground text-sm">vs</span>
            <Select items={options} value={b} onValueChange={(v) => v && setPickB(v)}>
              <SelectTrigger className="w-56" aria-label="Opción B">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options
                  .filter((o) => o.value !== a)
                  .map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      <ErrorAlert message={error} />
      {mode === 'period' && !prevRange && (
        <p className="text-muted-foreground text-sm">
          Elige un periodo distinto de “Todo” para compararlo con el anterior de la misma duración.
        </p>
      )}
      {ignoresScope && (scope.channel !== 'all' || scope.account) && (
        <p className="text-muted-foreground text-sm">
          En esta vista se comparan todos los canales; el filtro de canal y tienda de la barra superior no aplica.
        </p>
      )}
      {(mode === 'product' || mode === 'account') && (
        <p className="text-muted-foreground text-sm">
          {mode === 'product'
            ? `Compara dos productos dentro de ${scopeName}.`
            : `Compara tiendas de ${scope.channel === 'all' ? 'todos los canales' : CHANNEL_LABEL[scope.channel]}.`}{' '}
          Cambia el canal o la tienda arriba para verlos en otro lugar.
        </p>
      )}
      {mode !== 'period' && !loading && options.length < 2 && (
        <p className="text-muted-foreground text-sm">
          {mode === 'sku_channels'
            ? 'Este producto solo se vendió en un canal en el periodo; elige otro producto.'
            : 'Hacen falta al menos dos opciones con ventas en el periodo para comparar.'}
        </p>
      )}

      {ready ? (
        <>
        <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
          <Card>
            <CardContent className="overflow-x-auto pt-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Métrica</TableHead>
                    <TableHead className="text-right" style={{ color: A_COLOR }}>
                      {nameA}
                    </TableHead>
                    <TableHead className="text-right" style={{ color: B_COLOR }}>
                      {nameB}
                    </TableHead>
                    <TableHead className="text-right">Diferencia (A − B)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {METRICS.map((m) => {
                    const va = num(rowA, m.key)
                    const vb = num(rowB, m.key)
                    const diff = va != null && vb != null ? va - vb : null
                    const aWins = m.better === 'high' ? (va ?? 0) > (vb ?? 0) : (va ?? 0) < (vb ?? 0)
                    const winner =
                      m.better && va != null && vb != null && va !== vb ? (aWins ? 'A' : 'B') : null
                    const win = 'font-semibold text-green-700'
                    return (
                      <TableRow key={m.key}>
                        <TableCell>{m.label}</TableCell>
                        <TableCell className={`text-right ${winner === 'A' ? win : ''}`}>{m.fmt(va)}</TableCell>
                        <TableCell className={`text-right ${winner === 'B' ? win : ''}`}>{m.fmt(vb)}</TableCell>
                        <TableCell className={`text-right ${tone(diff)}`}>
                          {diff == null ? '—' : m.fmt(diff)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
              <p className="text-muted-foreground mt-3 text-xs">
                En verde, el mejor valor de cada métrica con ganador claro (más ventas, utilidad y margen; menos
                reembolsos).
              </p>
            </CardContent>
          </Card>

          <ChartCard
            title="Ventas y utilidad"
            csv={chartData.map((r) => ({ concepto: r.name, [nameA]: r.A, [nameB]: r.B }))}
            filename="versus-ventas-utilidad"
          >
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis tickFormatter={axisMoney} width={56} tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => money(Number(v))} />
                <Legend />
                <Bar dataKey="A" name={nameA} fill={A_COLOR} />
                <Bar dataKey="B" name={nameB} fill={B_COLOR} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>

        <ChartCard
          title={mode === 'period' ? 'Período actual vs anterior, día por día' : 'Evolución de las dos opciones'}
          subtitle="Líneas: A y B. Barras: diferencia (A − B). Toca la leyenda para ocultar una serie."
          csv={trendCsv}
          filename="versus-evolucion"
          actions={
            <div className="flex flex-wrap gap-1" role="group" aria-label="Métrica">
              {TREND_METRICS.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  aria-pressed={trendKey === m.key}
                  onClick={() => setTrendKey(m.key)}
                  className={`rounded border px-2 py-1 text-xs ${trendKey === m.key ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          }
        >
          <ErrorAlert message={trendError} />
          {trend.length === 0 ? (
            !trendError && <EmptyChart>{trendLoading ? 'Cargando…' : 'Sin datos para este filtro.'}</EmptyChart>
          ) : (
            <ComboChart data={trend} xKey="x" series={trendSeries} leftFormat={trendKey === 'units' ? axisNumber : axisMoney} />
          )}
        </ChartCard>
        </>
      ) : (
        !error && <p className="text-muted-foreground text-sm">{loading ? 'Cargando…' : 'Sin datos para comparar.'}</p>
      )}
    </div>
  )
}
