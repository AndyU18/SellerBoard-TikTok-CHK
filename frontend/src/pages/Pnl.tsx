import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Callout, ErrorAlert, Hint } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { ExpandableTable, type TreeRow } from '@/components/charts/ExpandableTable'
import { axisPct, ChartCard, EmptyChart, type CsvRow } from '@/components/charts/kit'
import { Waterfall, type WaterfallStep } from '@/components/charts/Waterfall'
import { api, CHANNEL_LABEL, CHANNELS, type Channel } from '@/lib/api'
import { analytics, type PnlRow, type Recurrence } from '@/lib/analytics'
import { count, money, neg } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type MoneyKey = Exclude<keyof PnlRow, 'margin'>

const ZERO: PnlRow = {
  gross_sales: 0,
  promo: 0,
  net_sales: 0,
  platform_fees: 0,
  shipping_cost: 0,
  refund_amount: 0,
  cogs: 0,
  gross_profit: 0,
  ad_spend: 0,
  contribution: 0,
  expenses: 0,
  net_profit: 0,
  margin: null,
}

/** Suma varias filas del estado de resultados (el margen se recalcula sobre ventas netas). */
function addRows(rows: PnlRow[]): PnlRow {
  const out = { ...ZERO }
  for (const r of rows) for (const k of Object.keys(ZERO) as (keyof PnlRow)[]) if (k !== 'margin') out[k] += r[k] ?? 0
  out.margin = out.net_sales ? out.net_profit / out.net_sales : null
  return out
}

/** Una columna de la tabla (un mes o el total) con su desglose por canal y sus unidades. */
type Col = { total: PnlRow; byChannel: Map<Channel, PnlRow>; units: number; unitsBy: Map<Channel, number> }

const ratio = (n: number, d: number) => (d ? n / d : null)
const pctText = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

const RECURRENCE: Record<Recurrence, string> = {
  one_time: 'Una vez',
  monthly: 'Mensual',
  annual: 'Anual (1/12 por mes)',
}

const SELECT = 'h-9 rounded-md border bg-white px-2 text-sm dark:bg-slate-900'

function monthsAgo(n: number): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 7)
}

function Expenses({ onChange }: { onChange: () => void }) {
  const [version, setVersion] = useState(0)
  const expenses = useAsync(() => analytics.expenses(), [version])
  const categories = useAsync(() => analytics.expenseCategories(), [])
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({
    category_id: 0,
    description: '',
    amount: '',
    recurrence: 'monthly' as Recurrence,
    start_date: new Date().toISOString().slice(0, 10),
    end_date: '',
    channel: '' as Channel | '',
  })

  const categoryId = form.category_id || categories.data?.[0]?.id || 0

  async function add() {
    setError(null)
    const amount = Number(form.amount)
    if (!form.description.trim() || !form.amount || Number.isNaN(amount) || amount < 0) {
      setError('Escribe una descripción y un monto válido')
      return
    }
    try {
      await analytics.addExpense({
        category_id: categoryId,
        description: form.description.trim(),
        amount,
        recurrence: form.recurrence,
        start_date: form.start_date,
        end_date: form.end_date || null,
        channel: form.channel || null,
      })
      setForm((f) => ({ ...f, description: '', amount: '', end_date: '' }))
      setVersion((v) => v + 1)
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo guardar el gasto')
    }
  }

  async function remove(id: number) {
    setError(null)
    try {
      await analytics.deleteExpense(id)
      setVersion((v) => v + 1)
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo quitar el gasto')
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Gastos indirectos</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <ErrorAlert message={expenses.error} />
        {error && (
          <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Categoría</span>
            <select
              className={SELECT}
              value={categoryId}
              onChange={(e) => setForm((f) => ({ ...f, category_id: Number(e.target.value) }))}
            >
              {(categories.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Descripción</span>
            <Input
              className="h-9 w-56"
              value={form.description}
              placeholder="Redo, nómina, contador…"
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Monto (USD)</span>
            <Input
              type="number"
              min={0}
              className="h-9 w-28"
              value={form.amount}
              onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Frecuencia</span>
            <select
              className={SELECT}
              value={form.recurrence}
              onChange={(e) => setForm((f) => ({ ...f, recurrence: e.target.value as Recurrence }))}
            >
              {(Object.keys(RECURRENCE) as Recurrence[]).map((r) => (
                <option key={r} value={r}>
                  {RECURRENCE[r]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Desde</span>
            <Input
              type="date"
              className="h-9 w-40"
              value={form.start_date}
              onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Hasta (opcional)</span>
            <Input
              type="date"
              className="h-9 w-40"
              value={form.end_date}
              onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Canal</span>
            <select
              className={SELECT}
              value={form.channel}
              onChange={(e) => setForm((f) => ({ ...f, channel: e.target.value as Channel | '' }))}
            >
              <option value="">Repartir entre canales</option>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  Solo {CHANNEL_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          <Button size="sm" onClick={add}>
            Agregar gasto
          </Button>
        </div>

        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Descripción</TableHead>
                <TableHead>Categoría</TableHead>
                <TableHead>Frecuencia</TableHead>
                <TableHead>Vigencia</TableHead>
                <TableHead>Canal</TableHead>
                <TableHead className="text-right">Monto</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(expenses.data?.length ?? 0) === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground py-8 text-center">
                    {expenses.loading ? 'Cargando…' : 'Aún no registras gastos indirectos.'}
                  </TableCell>
                </TableRow>
              )}
              {(expenses.data ?? []).map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="font-medium">{e.description}</TableCell>
                  <TableCell>{e.category ?? '—'}</TableCell>
                  <TableCell>{RECURRENCE[e.recurrence]}</TableCell>
                  <TableCell>
                    {e.start_date} → {e.end_date ?? 'sin fin'}
                  </TableCell>
                  <TableCell>{e.channel ? <ChannelBadge channel={e.channel} /> : 'Todos'}</TableCell>
                  <TableCell className="text-right">{money(e.amount)}</TableCell>
                  <TableCell>
                    <Button variant="ghost" size="sm" onClick={() => remove(e.id)}>
                      Quitar
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}

export function Pnl() {
  const scope = useScope()
  // El rango sale del filtro global (meses completos); "Todo" muestra los últimos 12 meses.
  const [fallback] = useState(() => ({ from: `${monthsAgo(11)}-01`, to: `${monthsAgo(0)}-01` }))
  const start = `${(scope.range.start ?? fallback.from).slice(0, 7)}-01`
  const end = `${(scope.range.end ?? fallback.to).slice(0, 7)}-01`
  const [version, setVersion] = useState(0)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const data = useAsync(() => analytics.pnl(start, end, scope.channelParam), [start, end, scope.channelParam, version])
  const unitsData = useAsync(
    () => api.timeseries('month', { start, end: scope.range.end, channel: scope.channelParam, account: scope.account }),
    [start, scope.range.end, scope.channelParam, scope.account],
  )
  const d = data.data
  const months = useMemo(() => d?.months ?? [], [d])

  const cols = useMemo<Col[]>(() => {
    const units = new Map<string, Map<Channel, number>>()
    for (const r of unitsData.data?.rows ?? []) {
      if (!r.period || !r.channel) continue
      const key = r.period.slice(0, 7)
      const m = units.get(key) ?? new Map<Channel, number>()
      m.set(r.channel, (m.get(r.channel) ?? 0) + r.units)
      units.set(key, m)
    }
    const monthCols = months.map<Col>((m) => {
      const unitsBy = units.get(m.month.slice(0, 7)) ?? new Map<Channel, number>()
      return {
        total: m.total,
        byChannel: new Map(m.channels.map((c) => [c.channel, c])),
        units: [...unitsBy.values()].reduce((s, v) => s + v, 0),
        unitsBy,
      }
    })
    if (!d || !monthCols.length) return []
    const byChannel = new Map<Channel, PnlRow>()
    const unitsBy = new Map<Channel, number>()
    for (const ch of CHANNELS) {
      const rows = monthCols.map((c) => c.byChannel.get(ch)).filter((r): r is PnlRow => !!r)
      if (rows.length) byChannel.set(ch, addRows(rows))
      const u = monthCols.reduce((s, c) => s + (c.unitsBy.get(ch) ?? 0), 0)
      if (u) unitsBy.set(ch, u)
    }
    return [...monthCols, { total: d.summary, byChannel, units: monthCols.reduce((s, c) => s + c.units, 0), unitsBy }]
  }, [d, months, unitsData.data])

  const columns = [...months.map((m) => m.month.slice(0, 7)), 'Total']
  const channelsPresent = CHANNELS.filter((ch) => cols.some((c) => c.byChannel.has(ch)))

  const tree = useMemo<TreeRow[]>(() => {
    if (!cols.length) return []
    const money$ = (cost: boolean) => (v: number) => money(cost ? neg(v) : v)
    /** Fila de dinero; se abre por canal. */
    const line = (key: MoneyKey, label: string, opts: { cost?: boolean; bold?: boolean; split?: boolean } = {}): TreeRow => {
      const f = money$(!!opts.cost)
      return {
        id: key,
        label,
        bold: opts.bold,
        cells: cols.map((c) => f(c.total[key])),
        negative: cols.map((c) => (opts.cost ? false : c.total[key] < 0)),
        children:
          opts.split === false
            ? undefined
            : channelsPresent.map((ch) => ({
                id: `${key}:${ch}`,
                label: CHANNEL_LABEL[ch],
                cells: cols.map((c) => f(c.byChannel.get(ch)?.[key] ?? 0)),
                negative: cols.map((c) => (opts.cost ? false : (c.byChannel.get(ch)?.[key] ?? 0) < 0)),
              })),
      }
    }
    /** Fila de indicador (sombreada) calculada con un cociente. */
    const kpi = (id: string, label: string, get: (r: PnlRow) => number | null): TreeRow => ({
      id,
      label,
      shaded: true,
      cells: cols.map((c) => pctText(get(c.total))),
      negative: cols.map((c) => (get(c.total) ?? 0) < 0),
    })
    return [
      line('gross_sales', 'Ventas'),
      {
        id: 'units',
        label: 'Unidades',
        cells: cols.map((c) => count(c.units)),
        children: channelsPresent.map((ch) => ({ id: `units:${ch}`, label: CHANNEL_LABEL[ch], cells: cols.map((c) => count(c.unitsBy.get(ch) ?? 0)) })),
      },
      line('promo', 'Promociones', { cost: true }),
      line('platform_fees', 'Comisiones de la plataforma', { cost: true }),
      line('shipping_cost', 'Envío', { cost: true }),
      line('refund_amount', 'Reembolsos', { cost: true }),
      line('cogs', 'Costo de bienes', { cost: true }),
      line('gross_profit', 'Ganancia bruta', { bold: true }),
      line('ad_spend', 'Publicidad', { cost: true }),
      line('expenses', 'Gastos indirectos', { cost: true }),
      line('net_profit', 'Ganancia neta', { bold: true }),
      kpi('tacos', 'TACOS (publicidad / ventas)', (r) => ratio(r.ad_spend, r.gross_sales)),
      kpi('refund_rate', 'Tasa de reembolso', (r) => ratio(r.refund_amount, r.gross_sales)),
      kpi('margin', 'Margen (sobre ventas netas)', (r) => r.margin),
      kpi('roi', 'ROI (ganancia / costo de bienes)', (r) => ratio(r.net_profit, r.cogs)),
    ]
  }, [cols, channelsPresent])

  const steps = useMemo<WaterfallStep[]>(() => {
    const s = d?.summary
    if (!s || !months.length) return []
    const cost = (id: string, label: string, v: number): WaterfallStep => ({ id, label, kind: 'delta', value: neg(v) })
    return [
      { id: 'gross_sales', label: 'Ventas', kind: 'total', value: s.gross_sales },
      cost('promo', 'Promociones', s.promo),
      cost('platform_fees', 'Comisiones', s.platform_fees),
      cost('shipping_cost', 'Envío', s.shipping_cost),
      cost('refund_amount', 'Reembolsos', s.refund_amount),
      cost('cogs', 'Costo de bienes', s.cogs),
      cost('ad_spend', 'Publicidad', s.ad_spend),
      cost('expenses', 'Gastos indirectos', s.expenses),
      { id: 'net_profit', label: 'Ganancia neta', kind: 'total', value: s.net_profit },
    ]
  }, [d, months])
  const [step, setStep] = useState<WaterfallStep | null>(null)

  const monthChart = months.map((m) => ({
    month: m.month.slice(0, 7),
    net_sales: m.total.net_sales,
    net_profit: m.total.net_profit,
    margin: m.total.margin,
  }))
  const monthSeries: ComboSeries[] = [
    { key: 'net_sales', label: 'Ventas netas', type: 'bar', color: '#3b82f6', format: money },
    { key: 'net_profit', label: 'Ganancia neta', type: 'bar', color: '#10b981', format: money },
    { key: 'margin', label: 'Margen (eje derecho)', type: 'line', axis: 'right', color: '#f59e0b', format: pctText },
  ]
  const tableCsv: CsvRow[] = tree.flatMap((r) => [r, ...(r.children ?? [])]).map((r) => ({ concepto: r.label, ...Object.fromEntries(columns.map((c, i) => [c, r.cells[i]])) }))

  async function download() {
    setDownloadError(null)
    try {
      const blob = await analytics.pnlCsv(start, end, scope.channelParam)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'estado_de_resultados.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'No se pudo descargar')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Estado de resultados</h1>
        <Button variant="outline" size="sm" onClick={download}>
          Descargar CSV
        </Button>
      </div>

      <ErrorAlert message={data.error} />
      {downloadError && <Callout tone="danger">{downloadError}</Callout>}
      <Callout tone="info">
        La utilidad de las demás pantallas es la <strong>contribución</strong> (antes de gastos indirectos). Aquí se
        restan los gastos fijos que registres abajo para llegar a la utilidad neta del negocio.
      </Callout>

      <ErrorAlert message={unitsData.error} />

      <ChartCard
        title="Estado de resultados"
        subtitle="Toca una fila con flecha (Ventas, Unidades, costos…) para abrirla por canal. Las filas grises son indicadores."
        csv={tableCsv}
        filename="estado-de-resultados-tabla"
      >
        {tree.length === 0 ? (
          <EmptyChart>{data.loading ? 'Cargando…' : 'Sin datos en este rango.'}</EmptyChart>
        ) : (
          <ExpandableTable columns={columns} rows={tree} />
        )}
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="De ventas a ganancia neta"
          subtitle="Todo el rango · toca una barra para ver su peso sobre las ventas"
          csv={steps.map((s) => ({ concepto: s.label, monto: s.value }))}
          filename="estado-de-resultados-cascada"
        >
          {steps.length === 0 ? <EmptyChart>{data.loading ? 'Cargando…' : 'Sin datos en este rango.'}</EmptyChart> : <Waterfall steps={steps} onStepClick={setStep} />}
          {step && d && (
            <p className="mt-2 text-sm">
              <strong>{step.label}:</strong> {money(step.value)}
              {d.summary.gross_sales ? ` · ${pctText(Math.abs(step.value) / d.summary.gross_sales)} de las ventas` : ''}
            </p>
          )}
        </ChartCard>

        <ChartCard
          title="Ventas netas, ganancia y margen por mes"
          subtitle="Barras: ventas netas y ganancia neta. Línea: margen (eje derecho)."
          csv={monthChart.map((r) => ({ ...r }))}
          filename="estado-de-resultados-mensual"
        >
          {monthChart.length === 0 ? (
            <EmptyChart>{data.loading ? 'Cargando…' : 'Sin datos en este rango.'}</EmptyChart>
          ) : (
            <ComboChart data={monthChart} xKey="month" series={monthSeries} rightFormat={axisPct} />
          )}
        </ChartCard>
      </div>

      {months.some((m) => m.expenses_by_category.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>Gastos indirectos por categoría</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Mes</TableHead>
                  <TableHead>Categorías</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {months
                  .filter((m) => m.expenses_by_category.length > 0)
                  .map((m) => (
                    <TableRow key={m.month}>
                      <TableCell>{m.month.slice(0, 7)}</TableCell>
                      <TableCell className="text-sm">
                        {m.expenses_by_category.map((c) => `${c.category}: ${money(c.amount)}`).join(' · ')}
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Expenses onChange={() => setVersion((v) => v + 1)} />

      <Hint>
        Un gasto puntual cuenta en el mes de su fecha; uno mensual, en cada mes de su vigencia; uno anual, en 1/12
        durante 12 meses. Si tiene canal, es todo de ese canal; si no, se reparte entre los canales según sus ventas
        netas del mes. {d?.note}
      </Hint>
    </div>
  )
}
