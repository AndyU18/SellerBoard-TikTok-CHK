import { useState } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money, tone } from '@/lib/format'
import { redo, type RedoOrigin } from '@/lib/redo'
import { useAsync } from '@/lib/useAsync'

const PERIODS = [
  { value: '30', label: '30 días' },
  { value: '90', label: '90 días' },
  { value: '180', label: '180 días' },
  { value: '365', label: '365 días' },
]

const ORIGIN: Record<RedoOrigin, { explain: string; pill: string }> = {
  tiktok_shop: { explain: 'Ya está en las ventas de la app.', pill: PILL.green },
  walmart: { explain: 'Ya está en las ventas de la app.', pill: PILL.green },
  tiktok_pendiente: {
    explain: 'Parece una orden de TikTok que la app todavía no ha sincronizado. Se acomodará sola al sincronizar.',
    pill: PILL.amber,
  },
  shopify: {
    explain: 'Orden de Shopify: esa tienda aún no está integrada, por eso no hay venta con la que compararla.',
    pill: PILL.blue,
  },
  otro: { explain: 'No coincide con ninguna venta conocida (órdenes viejas, otra tienda o sin número de orden).', pill: PILL.slate },
}

const usdAxis = (v: number) => `$${v.toLocaleString('en-US')}`
const shortDay = (d: string) => d.slice(5)

export function RedoOverview() {
  const [days, setDays] = useState('90')
  const data = useAsync(() => redo.overview(Number(days)), [days])
  const d = data.data
  const k = d?.kpis
  const monthly = d?.by_month ?? []
  const daily = d?.by_day ?? []
  const covered = (d?.coverage ?? []).filter((c) => c.origin !== 'tiktok_shop' && c.origin !== 'walmart')
  const coveredOk = (d?.coverage ?? []).filter((c) => c.origin === 'tiktok_shop' || c.origin === 'walmart')

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Redo: gasto en envíos</h1>
          {d && (
            <p className="text-muted-foreground text-xs">
              Del {d.window.start} al {d.window.end}
              {d.last_charge ? ` · último cargo: ${d.last_charge.slice(0, 10)}` : ''}
            </p>
          )}
        </div>
        <Segmented label="Periodo" value={days} options={PERIODS} onChange={setDays} />
      </div>

      <ErrorAlert message={data.error} />
      {data.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Gasto neto" value={money(k?.net_cost ?? 0)} hint="etiquetas + correcciones − créditos + comisiones" />
        <Kpi title="Etiquetas" value={money(k?.labels ?? 0)} hint={`${count(k?.orders ?? 0)} órdenes con etiqueta`} />
        <Kpi
          title="Correcciones del carrier"
          value={money(k?.surcharges ?? 0)}
          hint={`${pct1(k?.surcharge_rate)} sobre el costo de etiquetas (recargos por peso o medidas distintos)`}
          className={(k?.surcharges ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Anulaciones y créditos"
          value={money((k?.voids ?? 0) + (k?.claim_payouts ?? 0))}
          hint={`etiquetas anuladas ${money(k?.voids ?? 0)} · reclamos pagados ${money(k?.claim_payouts ?? 0)}`}
          className="text-green-700"
        />
        <Kpi
          title="Comisiones"
          value={money((k?.card_fees ?? 0) + (k?.claim_fees ?? 0))}
          hint={`tarjeta ${money(k?.card_fees ?? 0)} · reclamos ${money(k?.claim_fees ?? 0)}`}
        />
        <Kpi
          title="Costo por orden"
          value={money(k?.avg_cost_per_order)}
          hint={`promedio · mediana ${money(k?.median_cost_per_order)}`}
        />
        <Kpi
          title="Órdenes con corrección"
          value={pct1(k?.surcharge_orders_pct)}
          hint={`${count(k?.surcharge_orders ?? 0)} de ${count(k?.orders ?? 0)} órdenes`}
        />
        <Kpi
          title="Órdenes re-etiquetadas"
          value={count(k?.relabel_orders ?? 0)}
          hint="se compró más de una etiqueta para la misma orden"
          className={(k?.relabel_orders ?? 0) > 0 ? 'text-amber-600' : ''}
        />
      </div>

      {(k?.unassigned_cost ?? 0) !== 0 && (
        <Callout tone="info">
          {money(k?.unassigned_cost ?? 0)} de este gasto no tiene número de orden, por eso no cuenta en el costo por
          orden.
        </Callout>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Gasto por mes</CardTitle>
        </CardHeader>
        <CardContent className="h-80">
          {monthly.length === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              {data.loading ? 'Cargando…' : 'Aún no hay cargos de Redo.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={monthly}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="month" />
                <YAxis yAxisId="l" tickFormatter={usdAxis} width={72} />
                <YAxis yAxisId="r" orientation="right" tickFormatter={(v: number) => `$${v}`} width={48} />
                <Tooltip formatter={(v) => money(Number(v))} />
                <Legend />
                <Bar yAxisId="l" dataKey="labels" name="Etiquetas" stackId="m" fill="#2563eb" />
                <Bar yAxisId="l" dataKey="surcharges" name="Correcciones" stackId="m" fill="#f59e0b" />
                <Bar yAxisId="l" dataKey="voids" name="Anulaciones" stackId="m" fill="#16a34a" />
                <Line yAxisId="l" dataKey="net" name="Neto" stroke="#0f172a" strokeWidth={2} dot={false} />
                <Line
                  yAxisId="r"
                  dataKey="avg_per_order"
                  name="Costo promedio por orden"
                  stroke="#dc2626"
                  strokeDasharray="4 3"
                  dot={false}
                  connectNulls
                />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Gasto neto por día</CardTitle>
        </CardHeader>
        <CardContent className="h-64">
          {daily.length === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              {data.loading ? 'Cargando…' : 'Sin cargos en este periodo.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={daily}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tickFormatter={shortDay} minTickGap={24} />
                <YAxis tickFormatter={usdAxis} width={72} />
                <Tooltip formatter={(v) => money(Number(v))} labelFormatter={(l) => String(l)} />
                <Line dataKey="net" name="Gasto neto" stroke="#2563eb" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Por tipo de cargo</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tipo</TableHead>
                  <TableHead className="text-right">Cargos</TableHead>
                  <TableHead className="text-right">Monto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.by_subtype.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin cargos en este periodo.'}
                    </TableCell>
                  </TableRow>
                )}
                {(d?.by_subtype ?? []).map((s) => (
                  <TableRow key={s.subtype}>
                    <TableCell>
                      {s.label}
                      {!s.is_cost && <span className="text-muted-foreground ml-2 text-xs">(ajuste de saldo, no es costo)</span>}
                    </TableCell>
                    <TableCell className="text-right">{count(s.count)}</TableCell>
                    <TableCell className={`text-right ${s.amount < 0 ? 'text-green-700' : tone(s.amount)}`}>
                      {money(s.amount)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Motivos de las correcciones del carrier</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Motivo</TableHead>
                  <TableHead className="text-right">Veces</TableHead>
                  <TableHead className="text-right">Monto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.surcharge_reasons.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin correcciones en este periodo.'}
                    </TableCell>
                  </TableRow>
                )}
                {(d?.surcharge_reasons ?? []).map((r) => (
                  <TableRow key={r.reason}>
                    <TableCell className="max-w-xs break-words whitespace-normal">{r.reason}</TableCell>
                    <TableCell className="text-right">{count(r.count)}</TableCell>
                    <TableCell className="text-right">{money(r.amount)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Cobertura: etiquetas frente a las ventas de la app</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-muted-foreground text-sm">
            Cada orden con etiqueta se busca entre las ventas de la app. Las que no se encuentran no tienen ingreso con
            el cual medir su costo.
          </p>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Dónde está la orden</TableHead>
                  <TableHead>Qué significa</TableHead>
                  <TableHead className="text-right">Órdenes</TableHead>
                  <TableHead className="text-right">Monto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.coverage.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin órdenes con etiqueta en este periodo.'}
                    </TableCell>
                  </TableRow>
                )}
                {[...coveredOk, ...covered].map((c) => (
                  <TableRow key={c.origin}>
                    <TableCell>
                      <Pill label={c.label} className={ORIGIN[c.origin]?.pill ?? PILL.slate} />
                    </TableCell>
                    <TableCell className="text-muted-foreground max-w-md text-sm break-words whitespace-normal">
                      {ORIGIN[c.origin]?.explain ?? ''}
                    </TableCell>
                    <TableCell className="text-right">{count(c.orders)}</TableCell>
                    <TableCell className="text-right">{money(c.amount)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Hint>
        El gasto neto suma etiquetas, correcciones del carrier y comisiones, y resta las etiquetas anuladas y los
        reclamos pagados. Las recargas de saldo no son costo: son dinero que adelantas a Redo (ver Facturación).
      </Hint>
    </div>
  )
}
