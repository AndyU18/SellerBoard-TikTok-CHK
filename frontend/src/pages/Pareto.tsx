import { useMemo, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ErrorAlert } from '@/components/analytics/ui'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { axisMoney, axisPct, ChartCard, EmptyChart, TipBox } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, type AbcClass, type Filters, type ParetoRow } from '@/lib/api'
import { count, money, percent, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const CLASS_INFO: Record<AbcClass, { label: string; hint: string; badge: string }> = {
  A: { label: 'Clase A', hint: 'hasta el 80% del total', badge: 'bg-green-100 text-green-800' },
  B: { label: 'Clase B', hint: 'del 80% al 95%', badge: 'bg-amber-100 text-amber-800' },
  C: { label: 'Clase C', hint: 'el 5% restante', badge: 'bg-slate-100 text-slate-700' },
  loss: { label: 'Pierden dinero', hint: 'utilidad negativa', badge: 'bg-red-100 text-red-800' },
}
const ORDER: AbcClass[] = ['A', 'B', 'C', 'loss']
const CHART_LIMIT = 25

type CurveRow = { sku: string; name: string; value: number; cumulative: number }

/** Curva de Pareto: barras (valor) + línea acumulada en eje derecho, con referencias al 80% y 95% y leyenda que apaga series. */
function ParetoCurve({ data, label, onPick }: { data: CurveRow[]; label: string; onPick: (sku: string) => void }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const toggle = (k: string) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  const items = [
    { key: 'value', label, color: '#3b82f6', bar: true },
    { key: 'cumulative', label: 'Acumulado %', color: '#0f172a', bar: false },
  ]
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {items.map((i) => (
          <button
            key={i.key}
            type="button"
            aria-pressed={!hidden.has(i.key)}
            onClick={() => toggle(i.key)}
            className={`flex items-center gap-1.5 ${hidden.has(i.key) ? 'opacity-40' : ''}`}
          >
            <span
              className={`inline-block size-3 ${i.bar ? 'rounded-sm' : 'rounded-full border-2 bg-white'}`}
              style={i.bar ? { background: i.color } : { borderColor: i.color }}
            />
            {i.label}
          </button>
        ))}
        <span className="text-muted-foreground flex items-center gap-1.5">
          <span className="inline-block w-4 border-t-2 border-dashed border-green-600" /> 80% (clase A)
        </span>
        <span className="text-muted-foreground flex items-center gap-1.5">
          <span className="inline-block w-4 border-t-2 border-dashed border-amber-600" /> 95% (clase B)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={340}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="sku" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={70} />
          <YAxis yAxisId="v" tickFormatter={axisMoney} tick={{ fontSize: 11 }} width={56} />
          <YAxis yAxisId="p" orientation="right" domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} tick={{ fontSize: 11 }} width={44} />
          <Tooltip
            content={({ active, payload }) => {
              const d = active ? (payload?.[0]?.payload as CurveRow | undefined) : undefined
              return d ? (
                <TipBox
                  title={`${d.name} (${d.sku})`}
                  rows={[
                    { label, value: money(d.value), color: '#3b82f6' },
                    { label: 'Acumulado', value: `${d.cumulative.toFixed(1)}%`, color: '#0f172a' },
                  ]}
                />
              ) : null
            }}
          />
          <ReferenceLine yAxisId="p" y={80} stroke="#16a34a" strokeDasharray="4 4" />
          <ReferenceLine yAxisId="p" y={95} stroke="#d97706" strokeDasharray="4 4" />
          {!hidden.has('value') && (
            <Bar
              yAxisId="v"
              dataKey="value"
              name={label}
              fill="#3b82f6"
              radius={[3, 3, 0, 0]}
              cursor="pointer"
              onClick={(d: unknown) => {
                const sku = (d as { payload?: CurveRow }).payload?.sku
                if (sku) onPick(sku)
              }}
            />
          )}
          {!hidden.has('cumulative') && <Line yAxisId="p" dataKey="cumulative" name="Acumulado" stroke="#0f172a" dot={{ r: 3 }} strokeWidth={2} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

export function Pareto() {
  const [metric, setMetric] = useState<'profit' | 'sales'>('profit')
  const [selected, setSelected] = useState<string | null>(null)
  const scope = useScope()

  const filters = useMemo<Filters>(
    () => ({ ...scope.range, channel: scope.channelParam, account: scope.account }),
    [scope.range, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.pareto(metric, filters), [metric, filters])

  const rows = useMemo(() => data.data?.rows ?? [], [data.data])
  const summary = data.data?.summary ?? {}
  const total = ORDER.filter((c) => c !== 'loss').reduce((s, c) => s + (summary[c]?.value ?? 0), 0)
  const chartRows = rows.filter((r) => r.abc_class !== 'loss').slice(0, CHART_LIMIT)
  const valueOf = (r: ParetoRow) => (metric === 'profit' ? r.net_profit : r.gross_sales)
  const metricLabel = metric === 'profit' ? 'Utilidad' : 'Ventas'
  const curve: CurveRow[] = chartRows.map((r) => ({
    sku: r.sku,
    name: r.product_name ?? r.sku,
    value: valueOf(r),
    cumulative: (r.cumulative_share ?? 0) * 100,
  }))
  const picked = rows.find((r) => r.sku === selected) ?? null

  // Burbujas: volumen (unidades) vs margen, tamaño = ventas, color = clase ABC (sin ventas o sin margen no se dibujan).
  const bubbles = useMemo<BubblePoint[]>(
    () =>
      ORDER.flatMap((c) =>
        rows
          .filter((r) => r.abc_class === c && r.margin != null && r.units > 0)
          .map((r) => ({
            id: r.sku,
            label: r.product_name ?? r.sku,
            x: r.units,
            y: r.margin as number,
            z: Math.max(r.gross_sales, 1),
            group: CLASS_INFO[c].label,
          })),
      ),
    [rows],
  )

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Rendimiento de productos (Pareto ABC)</h1>

      <Tabs value={metric} onValueChange={(v) => setMetric(v as 'profit' | 'sales')}>
        <TabsList>
          <TabsTrigger value="profit">Por utilidad neta</TabsTrigger>
          <TabsTrigger value="sales">Por ventas</TabsTrigger>
        </TabsList>
      </Tabs>

      <ErrorAlert message={data.error} />

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

      <ChartCard
        title={`Curva de Pareto (top ${Math.min(CHART_LIMIT, chartRows.length)} por ${metric === 'profit' ? 'utilidad' : 'ventas'})`}
        subtitle="Barras: valor por producto. Línea: porcentaje acumulado (eje derecho). Toca una barra para ver el producto."
        csv={curve.map((r) => ({ sku: r.sku, producto: r.name, [metricLabel]: r.value, 'acumulado_%': Number(r.cumulative.toFixed(2)) }))}
        filename="pareto-curva"
      >
        {curve.length === 0 ? (
          <EmptyChart>{data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}</EmptyChart>
        ) : (
          <ParetoCurve data={curve} label={metricLabel} onPick={setSelected} />
        )}
      </ChartCard>

      <ChartCard
        title="Volumen vs margen"
        subtitle="Cada burbuja es un producto: unidades vendidas (horizontal), margen (vertical), tamaño = ventas, color = clase ABC. Toca una burbuja para ver el producto."
        csv={bubbles.map((b) => ({ sku: b.id, producto: b.label, unidades: b.x, margen: b.y, ventas: b.z, clase: b.group }))}
        filename="pareto-volumen-margen"
      >
        {bubbles.length === 0 ? (
          <EmptyChart>{data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}</EmptyChart>
        ) : (
          <BubbleChart
            points={bubbles}
            xLabel="Unidades"
            yLabel="Margen"
            zLabel="Ventas"
            xFormat={count}
            yFormat={axisPct}
            zFormat={money}
            yRef={0}
            onPointClick={(p) => setSelected(p.id)}
          />
        )}
      </ChartCard>

      {picked && (
        <Card>
          <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-4 text-sm">
            <div>
              <div className="font-medium">{picked.product_name ?? picked.sku}</div>
              <div className="text-muted-foreground text-xs">
                {picked.sku} · {CLASS_INFO[picked.abc_class].label}
              </div>
            </div>
            <div className="flex flex-wrap gap-4 tabular-nums">
              <span>{count(picked.units)} unid.</span>
              <span>{money(picked.gross_sales)} ventas</span>
              <span className={tone(picked.net_profit)}>{money(picked.net_profit)} utilidad</span>
              <span className={tone(picked.margin)}>{percent(picked.margin)} margen</span>
              <button type="button" onClick={() => setSelected(null)} className="text-muted-foreground hover:underline">
                Cerrar
              </button>
            </div>
          </CardContent>
        </Card>
      )}

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
                <TableRow
                  key={r.sku}
                  data-state={r.sku === selected ? 'selected' : undefined}
                  onClick={() => setSelected(r.sku)}
                  className="cursor-pointer"
                >
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
