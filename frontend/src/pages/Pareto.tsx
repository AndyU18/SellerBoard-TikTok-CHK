import { useMemo, useState } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { FilterBar, type ChannelFilter } from '@/components/FilterBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, type AbcClass, type Filters } from '@/lib/api'
import { count, money, percent, presetRange, tone, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const CLASS_INFO: Record<AbcClass, { label: string; hint: string; badge: string }> = {
  A: { label: 'Clase A', hint: 'hasta el 80% del total', badge: 'bg-green-100 text-green-800' },
  B: { label: 'Clase B', hint: 'del 80% al 95%', badge: 'bg-amber-100 text-amber-800' },
  C: { label: 'Clase C', hint: 'el 5% restante', badge: 'bg-slate-100 text-slate-700' },
  loss: { label: 'Pierden dinero', hint: 'utilidad negativa', badge: 'bg-red-100 text-red-800' },
}
const ORDER: AbcClass[] = ['A', 'B', 'C', 'loss']
const CHART_LIMIT = 25

export function Pareto() {
  const [metric, setMetric] = useState<'profit' | 'sales'>('profit')
  const [preset, setPreset] = useState<Preset>('90d')
  const [channel, setChannel] = useState<ChannelFilter>('all')

  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: channel === 'all' ? '' : channel }),
    [preset, channel],
  )
  const data = useAsync(() => api.pareto(metric, filters), [metric, filters])

  const rows = data.data?.rows ?? []
  const summary = data.data?.summary ?? {}
  const total = ORDER.filter((c) => c !== 'loss').reduce((s, c) => s + (summary[c]?.value ?? 0), 0)
  const chartRows = rows.filter((r) => r.abc_class !== 'loss').slice(0, CHART_LIMIT)
  const valueOf = (r: (typeof rows)[number]) => (metric === 'profit' ? r.net_profit : r.gross_sales)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Rendimiento de productos (Pareto ABC)</h1>
        <FilterBar preset={preset} onPreset={setPreset} channel={channel} onChannel={setChannel} />
      </div>

      <Tabs value={metric} onValueChange={(v) => setMetric(v as 'profit' | 'sales')}>
        <TabsList>
          <TabsTrigger value="profit">Por utilidad neta</TabsTrigger>
          <TabsTrigger value="sales">Por ventas</TabsTrigger>
        </TabsList>
      </Tabs>

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {ORDER.map((c) => {
          const s = summary[c]
          if (!s && c === 'loss') return null
          return (
            <Card key={c}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm font-medium">
                  <span className={`rounded px-2 py-0.5 text-xs ${CLASS_INFO[c].badge}`}>{CLASS_INFO[c].label}</span>
                  <span className="text-muted-foreground text-xs font-normal">{CLASS_INFO[c].hint}</span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold">{s?.products ?? 0} productos</div>
                <div className={`text-sm ${tone(s?.value)}`}>
                  {money(s?.value ?? 0)}
                  {c !== 'loss' && total > 0 && ` · ${percent((s?.value ?? 0) / total)} del total`}
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            Curva de Pareto (top {Math.min(CHART_LIMIT, chartRows.length)} por {metric === 'profit' ? 'utilidad' : 'ventas'})
          </CardTitle>
        </CardHeader>
        <CardContent className="h-80">
          {chartRows.length === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              {data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartRows.map((r) => ({ ...r, value: valueOf(r), cumulative: (r.cumulative_share ?? 0) * 100 }))}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="sku" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={70} />
                <YAxis yAxisId="v" tickFormatter={(v: number) => `$${v.toLocaleString('en-US')}`} width={72} />
                <YAxis yAxisId="p" orientation="right" domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} width={44} />
                <Tooltip
                  formatter={(v, name) => (name === 'Acumulado' ? `${Number(v).toFixed(1)}%` : money(Number(v)))}
                />
                <ReferenceLine yAxisId="p" y={80} stroke="#16a34a" strokeDasharray="4 4" />
                <ReferenceLine yAxisId="p" y={95} stroke="#d97706" strokeDasharray="4 4" />
                <Bar yAxisId="v" dataKey="value" name={metric === 'profit' ? 'Utilidad' : 'Ventas'} fill="#3b82f6" />
                <Line yAxisId="p" dataKey="cumulative" name="Acumulado" stroke="#0f172a" dot={false} strokeWidth={2} />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>Producto</TableHead>
                <TableHead>Clase</TableHead>
                <TableHead className="text-right">Unidades</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">Utilidad neta</TableHead>
                <TableHead className="text-right">Margen</TableHead>
                <TableHead className="text-right">% del total</TableHead>
                <TableHead className="text-right">Acumulado</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r, i) => (
                <TableRow key={r.sku}>
                  <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                  <TableCell>
                    <div className="flex min-w-48 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground text-xs">{r.sku}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className={`rounded px-2 py-0.5 text-xs ${CLASS_INFO[r.abc_class].badge}`}>
                      {r.abc_class === 'loss' ? 'Pérdida' : r.abc_class}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">{count(r.units)}</TableCell>
                  <TableCell className="text-right">{money(r.gross_sales)}</TableCell>
                  <TableCell className={`text-right font-medium ${tone(r.net_profit)}`}>{money(r.net_profit)}</TableCell>
                  <TableCell className={`text-right ${tone(r.margin)}`}>{percent(r.margin)}</TableCell>
                  <TableCell className="text-right">{percent(r.share)}</TableCell>
                  <TableCell className="text-right">{percent(r.cumulative_share)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
