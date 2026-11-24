import { useMemo } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, pct1 } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { Donut } from '@/components/charts/Donut'
import { HeatGrid } from '@/components/charts/HeatGrid'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money, tone } from '@/lib/format'
import { daysFromPreset, redo, type RedoOrigin } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const WEEKDAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const DAY_MS = 86_400_000

const isoLocal = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const parseDay = (s: string) => new Date(`${s}T00:00:00`)

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

const shortDay = (v: string | number) => String(v).slice(5)

export function RedoOverview() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const data = useAsync(() => redo.overview(days), [days])
  const d = data.data
  const k = d?.kpis
  const monthly = d?.by_month ?? []
  const covered = (d?.coverage ?? []).filter((c) => c.origin !== 'tiktok_shop' && c.origin !== 'walmart')
  const coveredOk = (d?.coverage ?? []).filter((c) => c.origin === 'tiktok_shop' || c.origin === 'walmart')
  const empty = (msg: string) => <EmptyChart>{data.loading ? 'Cargando…' : msg}</EmptyChart>

  // Serie diaria sin huecos (los días sin cargos cuentan 0) con promedio móvil de 7 días.
  const daily = useMemo(() => {
    const rows = d?.by_day ?? []
    if (!rows.length) return []
    const byDate = new Map(rows.map((r) => [r.date, r.net]))
    const out: { date: string; net: number; avg7: number }[] = []
    const last = parseDay(rows[rows.length - 1].date)
    const vals: number[] = []
    for (let t = parseDay(rows[0].date); t <= last; t = new Date(t.getTime() + DAY_MS)) {
      const key = isoLocal(t)
      const net = byDate.get(key) ?? 0
      vals.push(net)
      const win = vals.slice(-7)
      out.push({ date: key, net, avg7: Math.round((win.reduce((a, v) => a + v, 0) / win.length) * 100) / 100 })
    }
    return out
  }, [d?.by_day])

  // Gasto neto por día de la semana (filas) × semana (columnas, empiezan en lunes).
  const heat = useMemo(() => {
    const rows = d?.by_day ?? []
    if (!rows.length) return null
    const monday = (s: string) => {
      const t = parseDay(s)
      return new Date(t.getTime() - ((t.getDay() + 6) % 7) * DAY_MS)
    }
    const first = monday(rows[0].date)
    const weeks = Math.round((monday(rows[rows.length - 1].date).getTime() - first.getTime()) / (7 * DAY_MS)) + 1
    const values = WEEKDAYS.map(() => Array.from({ length: weeks }, () => 0))
    for (const r of rows) {
      const t = parseDay(r.date)
      const w = Math.round((monday(r.date).getTime() - first.getTime()) / (7 * DAY_MS))
      values[(t.getDay() + 6) % 7][w] += r.net
    }
    const cols = Array.from({ length: weeks }, (_, i) => isoLocal(new Date(first.getTime() + i * 7 * DAY_MS)).slice(5))
    return { values, cols }
  }, [d?.by_day])

  const costSlices = (d?.by_subtype ?? []).filter((s) => s.is_cost).map((s) => ({ id: s.subtype, label: s.label, value: s.amount }))
  const covSlices = (d?.coverage ?? []).map((c) => ({ id: c.origin, label: c.label, value: c.orders }))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Redo: gasto en envíos</h1>
        {d && (
          <p className="text-muted-foreground text-xs">
            Del {d.window.start} al {d.window.end} (período global: {days} días)
            {d.last_charge ? ` · último cargo: ${d.last_charge.slice(0, 10)}` : ''}
          </p>
        )}
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

      <ChartCard
        title="Gasto por mes"
        subtitle="Barras: etiquetas, correcciones y anulaciones (apilan). Línea (eje derecho): costo promedio por orden. Toca la leyenda para apagar series."
        filename="redo-gasto-por-mes"
        csv={monthly.map((m) => ({
          Mes: m.month,
          Etiquetas: m.labels,
          Correcciones: m.surcharges,
          Anulaciones: m.voids,
          Neto: m.net,
          Ordenes: m.orders,
          'Costo promedio por orden': m.avg_per_order,
        }))}
      >
        {monthly.length === 0 ? (
          empty('Aún no hay cargos de Redo.')
        ) : (
          <ComboChart
            data={monthly}
            xKey="month"
            rightFormat={(v) => `$${v.toFixed(0)}`}
            series={[
              { key: 'labels', label: 'Etiquetas', type: 'bar', stack: 'm', color: '#2563eb', format: money },
              { key: 'surcharges', label: 'Correcciones', type: 'bar', stack: 'm', color: COLOR.amber, format: money },
              { key: 'voids', label: 'Anulaciones', type: 'bar', stack: 'm', color: COLOR.good, format: money },
              { key: 'avg_per_order', label: 'Costo promedio por orden', type: 'line', axis: 'right', color: COLOR.bad, format: money },
            ]}
          />
        )}
      </ChartCard>

      <ChartCard
        title="Gasto neto por día"
        subtitle="Barras: gasto del día. Línea: promedio móvil de 7 días (los días sin cargos cuentan como $0)."
        filename="redo-gasto-diario"
        csv={daily.map((r) => ({ Fecha: r.date, 'Gasto neto': r.net, 'Promedio 7 dias': r.avg7 }))}
      >
        {daily.length === 0 ? (
          empty('Sin cargos en este período.')
        ) : (
          <ComboChart
            data={daily}
            xKey="date"
            xFormat={shortDay}
            height={280}
            series={[
              { key: 'net', label: 'Gasto neto', type: 'bar', color: '#93c5fd', format: money },
              { key: 'avg7', label: 'Promedio móvil 7 días', type: 'line', color: '#1d4ed8', format: money },
            ]}
          />
        )}
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Por tipo de cargo"
          subtitle="Solo costos (los créditos y ajustes de saldo no entran)."
          filename="redo-tipo-de-cargo"
          csv={(d?.by_subtype ?? []).map((s) => ({ Tipo: s.label, Cargos: s.count, Monto: s.amount, 'Es costo': s.is_cost ? 'sí' : 'no' }))}
        >
          {costSlices.length === 0 ? empty('Sin cargos en este período.') : <Donut slices={costSlices} format={money} centerLabel="costo total" />}
        </ChartCard>

        <ChartCard
          title="Gasto neto: día de la semana × semana"
          subtitle="Cada columna es una semana (empieza el lunes, MM-DD). Más oscuro = más gasto."
          filename="redo-mapa-de-calor"
          csv={
            heat
              ? heat.values.flatMap((row, ri) => row.map((v, ci) => ({ Dia: WEEKDAYS[ri], 'Semana desde': heat.cols[ci], 'Gasto neto': Math.round(v * 100) / 100 })))
              : []
          }
        >
          {heat ? <HeatGrid rowLabels={WEEKDAYS} colLabels={heat.cols} values={heat.values} format={money} /> : empty('Sin cargos en este período.')}
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Detalle por tipo de cargo</CardTitle>
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

      <ChartCard
        title="Cobertura: etiquetas frente a las ventas de la app"
        subtitle="Cada orden con etiqueta se busca entre las ventas de la app. Las que no se encuentran no tienen ingreso con el cual medir su costo."
        filename="redo-cobertura"
        csv={(d?.coverage ?? []).map((c) => ({ Origen: c.label, Ordenes: c.orders, Monto: c.amount }))}
      >
        <div className="space-y-4">
          {covSlices.length === 0 ? empty('Sin órdenes con etiqueta en este período.') : <Donut slices={covSlices} format={count} centerLabel="órdenes" />}
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
        </div>
      </ChartCard>

      <Hint>
        El gasto neto suma etiquetas, correcciones del carrier y comisiones, y resta las etiquetas anuladas y los
        reclamos pagados. Las recargas de saldo no son costo: son dinero que adelantas a Redo (ver Facturación).
      </Hint>
    </div>
  )
}
