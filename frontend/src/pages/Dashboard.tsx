import { useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChannelBadge } from '@/components/ChannelBadge'
import { FilterBar, type ChannelFilter } from '@/components/FilterBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { api, CHANNEL_COLOR, CHANNEL_LABEL, CHANNELS, type Filters, type ProfitRow } from '@/lib/api'
import { change, count, money, neg, percent, presetRange, previousRange, tone, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

/** Variación contra el periodo anterior (verde si sube, rojo si baja). */
function Delta({ value }: { value: number | null }) {
  if (value == null) return null
  const up = value >= 0
  return (
    <span className={`text-xs font-medium ${up ? 'text-green-700' : 'text-red-600'}`}>
      {up ? '▲' : '▼'} {Math.abs(value * 100).toFixed(1)}% vs periodo anterior
    </span>
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

function Stat({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className={`text-sm font-medium ${className}`}>{value}</div>
    </div>
  )
}

function SummaryCard({
  title,
  row,
  delta = null,
}: {
  title: ReactNode
  row: ProfitRow
  delta?: number | null
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <div className={`text-2xl font-semibold ${tone(row.net_profit)}`}>{money(row.net_profit)}</div>
          <Delta value={delta} />
        </div>
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Unidades" value={count(row.units)} />
          <Stat label="Utilidad / unid." value={money(row.unit_profit)} className={tone(row.unit_profit)} />
          <Stat label="Margen" value={percent(row.margin)} className={tone(row.margin)} />
        </div>
      </CardContent>
    </Card>
  )
}

interface PlLine {
  label: string
  text: (t: ProfitRow) => string
  /** Valor numérico para colorear en rojo cuando es negativo (solo líneas clave). */
  signed?: (t: ProfitRow) => number | null
}

const PL_LINES: PlLine[] = [
  { label: 'Ventas', text: (t) => money(t.gross_sales) },
  { label: 'Unidades', text: (t) => count(t.units) },
  { label: 'Reembolsos', text: (t) => money(neg(t.refund_amount)) },
  { label: 'Promociones', text: (t) => money(neg(t.promo)) },
  { label: 'Publicidad', text: (t) => money(neg(t.ad_spend)) },
  { label: 'Envío', text: (t) => money(neg(t.shipping_cost)) },
  { label: 'Comisiones', text: (t) => money(neg(t.platform_fees)) },
  { label: 'Costo de productos', text: (t) => money(neg(t.cogs)) },
  { label: 'Utilidad neta', text: (t) => money(t.net_profit), signed: (t) => t.net_profit },
  { label: 'Utilidad por unidad', text: (t) => money(t.unit_profit), signed: (t) => t.unit_profit },
  { label: 'Margen', text: (t) => percent(t.margin) },
  { label: 'ROI', text: (t) => percent(t.roi) },
  { label: 'TACoS', text: (t) => percent(t.tacos) },
]

export function Dashboard() {
  const [preset, setPreset] = useState<Preset>('90d')
  const [channel, setChannel] = useState<ChannelFilter>('all')

  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: channel === 'all' ? '' : channel }),
    [preset, channel],
  )
  const interval = preset === '30d' ? 'day' : 'month'

  const byChannel = useAsync(() => api.profit('channel', filters), [filters])
  const series = useAsync(() => api.timeseries(interval, filters), [filters, interval])
  const byProduct = useAsync(() => api.profit('product', filters), [filters])
  const previous = useAsync(async () => {
    const range = previousRange(preset)
    return range ? api.profit('channel', { ...range, channel: filters.channel }) : null
  }, [preset, filters.channel])

  const productRows = byProduct.data?.rows ?? []
  const topProducts = [...productRows].sort((a, b) => b.net_profit - a.net_profit).slice(0, 5)
  const lossProducts = productRows.filter((r) => r.net_profit < 0).sort((a, b) => a.net_profit - b.net_profit).slice(0, 5)
  const noCost = productRows.filter((r) => r.cogs === 0 && r.units > 0)
  const noCostSales = noCost.reduce((s, r) => s + r.gross_sales, 0)
  const totalSales = byChannel.data?.totals?.gross_sales ?? 0
  const prevByChannel = new Map((previous.data?.rows ?? []).map((r) => [r.channel, r]))
  const prevTotals = previous.data?.totals ?? null

  const chartData = useMemo(() => {
    const byPeriod = new Map<string, Record<string, number | string>>()
    for (const r of series.data?.rows ?? []) {
      if (!r.period || !r.channel) continue
      const entry = byPeriod.get(r.period) ?? { period: r.period }
      entry[r.channel] = r.net_profit
      byPeriod.set(r.period, entry)
    }
    return [...byPeriod.values()].sort((a, b) => String(a.period).localeCompare(String(b.period)))
  }, [series.data])

  const rows = byChannel.data?.rows ?? []
  const totals = byChannel.data?.totals ?? null
  const error = byChannel.error ?? series.error

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Resumen ejecutivo</h1>
        <FilterBar preset={preset} onPreset={setPreset} channel={channel} onChannel={setChannel} />
      </div>

      {error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {error}. ¿Está corriendo el backend en el puerto 8000?
        </div>
      )}

      {noCost.length > 0 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>La utilidad está inflada:</strong> {noCost.length} productos vendidos no tienen costo (COGS), el{' '}
          {totalSales > 0 ? ((noCostSales / totalSales) * 100).toFixed(0) : 0}% de las ventas del periodo. Complétalos en
          la sección Costos (COGS).
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {totals && <SummaryCard title="Total" row={totals} delta={prevTotals ? change(totals.net_profit, prevTotals.net_profit) : null} />}
        {CHANNELS.map((c) => {
          const row = rows.find((r) => r.channel === c)
          const prev = prevByChannel.get(c)
          return row ? (
            <SummaryCard
              key={c}
              title={<ChannelBadge channel={c} />}
              row={row}
              delta={prev ? change(row.net_profit, prev.net_profit) : null}
            />
          ) : null
        })}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <ProductList title="Top 5 productos por utilidad" rows={topProducts} empty="Sin ventas en este periodo." />
        <ProductList title="Productos con pérdida" rows={lossProducts} empty="Ningún producto pierde dinero en este periodo." />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_20rem]">
        <Card>
          <CardHeader>
            <CardTitle>Utilidad neta por canal ({interval === 'day' ? 'diaria' : 'mensual'})</CardTitle>
          </CardHeader>
          <CardContent className="h-80">
            {chartData.length === 0 ? (
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                {series.loading ? 'Cargando…' : 'Sin datos para este filtro.'}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="period" tickFormatter={(p: string) => p.slice(interval === 'day' ? 5 : 0, interval === 'day' ? 10 : 7)} />
                  <YAxis tickFormatter={(v: number) => `$${v.toLocaleString('en-US')}`} width={72} />
                  <Tooltip formatter={(v) => money(Number(v))} />
                  <Legend />
                  <ReferenceLine y={0} stroke="#888" />
                  {CHANNELS.map((c) => (
                    <Bar key={c} dataKey={c} name={CHANNEL_LABEL[c]} stackId="profit" fill={CHANNEL_COLOR[c]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Estado de resultados</CardTitle>
          </CardHeader>
          <CardContent>
            {totals ? (
              <dl className="divide-y text-sm">
                {PL_LINES.map((l) => (
                  <div
                    key={l.label}
                    className={`flex justify-between py-1.5 ${l.signed ? 'font-semibold' : ''}`}
                  >
                    <dt>{l.label}</dt>
                    <dd className={l.signed ? tone(l.signed(totals)) : ''}>{l.text(totals)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-muted-foreground text-sm">Sin datos.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
