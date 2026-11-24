import { useMemo, useState } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { ErrorAlert, Hint } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { FunnelBars, type FunnelStage } from '@/components/charts/FunnelBars'
import { axisNumber, axisPct, ChartCard, EmptyChart } from '@/components/charts/kit'
import { api, type Filters, type FunnelResponse } from '@/lib/api'
import { chartdataDinero, shortDate } from '@/lib/chartdata_dinero'
import { useScope } from '@/lib/scope'
import { count, money, percent, previousRange } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const SOURCE_LABEL: Record<string, string> = { VIDEO: 'Videos', LIVE: 'LIVE', PRODUCT_CARD: 'Tarjeta de producto' }

function Kpi({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold">{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

type Delivery = { orders: number; shipped: number; delivered: number } | null

/** Etapas del embudo con los datos que existen: tráfico de TikTok (impresiones, vistas, órdenes) y entregas del envío. */
function stagesOf(f: FunnelResponse | null, delivery: Delivery): FunnelStage[] {
  if (!f) return []
  const t = f.totals
  const out: FunnelStage[] = [
    { id: 'impressions', label: 'Impresiones de producto', value: t.impressions },
    { id: 'page_views', label: 'Vistas de la página del producto', value: t.page_views },
    { id: 'orders', label: 'Órdenes', value: t.orders },
  ]
  if (delivery && delivery.delivered > 0) out.push({ id: 'delivered', label: 'Entregadas', value: delivery.delivered })
  return out
}

export function Funnel() {
  const scope = useScope()
  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const prev = useMemo(() => previousRange(scope.preset), [scope.preset])
  const prevFilters = useMemo<Filters | null>(
    () => (prev ? { start: prev.start, end: prev.end, channel: scope.channelParam, account: scope.account } : null),
    [prev, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.funnel(filters), [filters])
  const before = useAsync(() => (prevFilters ? api.funnel(prevFilters) : Promise.resolve(null)), [prevFilters])
  const delivery = useAsync(() => chartdataDinero.funnelDelivery(filters), [filters])
  const deliveryPrev = useAsync(() => (prevFilters ? chartdataDinero.funnelDelivery(prevFilters) : Promise.resolve(null)), [prevFilters])
  const [picked, setPicked] = useState<FunnelStage | null>(null)

  const t = data.data?.totals
  const stages = useMemo(() => stagesOf(data.data, delivery.data), [data.data, delivery.data])
  const prevStages = useMemo(() => stagesOf(before.data, deliveryPrev.data), [before.data, deliveryPrev.data])
  const hasPrev = prevStages.length > 0 && prevStages[0].value > 0

  // Comparación: cada etapa y la tasa de paso desde la etapa anterior, contra el período previo.
  const compare = stages.map((s, i) => {
    const p = prevStages.find((x) => x.id === s.id)
    const rate = i > 0 && stages[i - 1].value > 0 ? s.value / stages[i - 1].value : null
    const pi = p ? prevStages.findIndex((x) => x.id === s.id) : -1
    const prevRate = pi > 0 && prevStages[pi - 1].value > 0 && p ? p.value / prevStages[pi - 1].value : null
    return { ...s, prev: p?.value ?? null, rate, prevRate }
  })
  const rateChart = compare
    .filter((c) => c.rate != null)
    .map((c) => ({ step: `→ ${c.label}`, actual: c.rate, anterior: c.prevRate }))

  const csv = compare.map((c) => ({
    etapa: c.label,
    actual: c.value,
    periodo_anterior: c.prev,
    tasa_de_paso: c.rate,
    tasa_de_paso_anterior: c.prevRate,
  }))
  const daily = useMemo(() => (data.data?.daily ?? []).map((d) => ({ ...d, conv: d.conversion ?? 0 })), [data.data])

  const sourceValue = (s: FunnelResponse['by_source'][number], id: string) =>
    id === 'impressions' ? s.impressions : id === 'page_views' ? s.page_views : id === 'orders' ? s.orders : 0

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Embudo de tráfico y conversión</h1>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="El embudo de tráfico" />

      <ErrorAlert message={data.error} />
      <ErrorAlert message={delivery.error} />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Tasa de clics (CTR)" value={percent(t?.ctr)} hint="vistas de producto / impresiones" />
        <Kpi title="Conversión" value={percent(t?.conversion)} hint="compradores / vistas de producto" />
        <Kpi title="Ventas (GMV)" value={money(t?.gmv ?? 0)} hint={`${count(t?.orders ?? 0)} órdenes`} />
        <Kpi title="Ticket promedio" value={money(t?.aov)} hint="GMV / órdenes" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Embudo"
          subtitle="Entre etapas se muestra el porcentaje que pasa a la siguiente. Clic en una etapa para ver su reparto por origen."
          csv={csv}
          filename="embudo"
        >
          {stages.length === 0 ? (
            <EmptyChart>{data.loading ? 'Cargando…' : 'Sin tráfico. Sincroniza TikTok Shop desde Importar.'}</EmptyChart>
          ) : (
            <>
              <FunnelBars stages={stages} onStageClick={setPicked} />
              {picked && picked.id !== 'delivered' && (
                <div className="mt-3 rounded-md border p-3 text-sm">
                  <div className="mb-1 flex items-center justify-between font-medium">
                    <span>{picked.label} por origen</span>
                    <button type="button" onClick={() => setPicked(null)} className="text-muted-foreground text-xs hover:underline">
                      Cerrar
                    </button>
                  </div>
                  <ul className="divide-y">
                    {(data.data?.by_source ?? []).map((s) => (
                      <li key={s.source} className="flex justify-between py-1">
                        <span>{SOURCE_LABEL[s.source] ?? s.source}</span>
                        <span className="tabular-nums">{count(sourceValue(s, picked.id))}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="text-muted-foreground mt-2 text-xs">
                Carrito no aparece: TikTok no entrega el paso entre la vista del producto y la orden. «Entregadas» sale de los
                envíos registrados en tu tienda, que pueden no coincidir con todas las órdenes del tráfico.
              </p>
            </>
          )}
        </ChartCard>

        <ChartCard
          title="Contra el período anterior"
          subtitle={hasPrev ? 'Tasa de paso entre etapas: este período vs el anterior de la misma duración.' : 'Elige 30 o 90 días arriba para comparar; el tráfico de TikTok solo se guarda 90 días.'}
          csv={csv}
          filename="embudo-comparacion"
        >
          {!hasPrev ? (
            <EmptyChart>{before.loading ? 'Cargando…' : 'No hay un período anterior con datos para comparar.'}</EmptyChart>
          ) : (
            <>
              <ComboChart
                data={rateChart}
                xKey="step"
                height={220}
                leftFormat={axisPct}
                series={[
                  { key: 'actual', label: 'Este período', type: 'bar', color: '#3b82f6', format: (v) => percent(v) },
                  { key: 'anterior', label: 'Período anterior', type: 'bar', color: '#94a3b8', format: (v) => percent(v) },
                ]}
              />
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Etapa</TableHead>
                    <TableHead className="text-right">Actual</TableHead>
                    <TableHead className="text-right">Anterior</TableHead>
                    <TableHead className="text-right">Cambio</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {compare.map((c) => {
                    const ch = c.prev ? (c.value - c.prev) / c.prev : null
                    return (
                      <TableRow key={c.id}>
                        <TableCell>{c.label}</TableCell>
                        <TableCell className="text-right">{count(c.value)}</TableCell>
                        <TableCell className="text-right">{count(c.prev)}</TableCell>
                        <TableCell className={`text-right ${ch == null ? '' : ch >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                          {ch == null ? '—' : `${ch >= 0 ? '+' : ''}${(ch * 100).toFixed(0)}%`}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </>
          )}
        </ChartCard>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Por origen del tráfico</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Origen</TableHead>
                <TableHead className="text-right">Impresiones</TableHead>
                <TableHead className="text-right">Vistas</TableHead>
                <TableHead className="text-right">CTR</TableHead>
                <TableHead className="text-right">Compradores</TableHead>
                <TableHead className="text-right">Conversión</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">% de ventas</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data.data?.by_source ?? []).map((s) => (
                <TableRow key={s.source}>
                  <TableCell className="font-medium">{SOURCE_LABEL[s.source] ?? s.source}</TableCell>
                  <TableCell className="text-right">{count(s.impressions)}</TableCell>
                  <TableCell className="text-right">{count(s.page_views)}</TableCell>
                  <TableCell className="text-right">{percent(s.ctr)}</TableCell>
                  <TableCell className="text-right">{count(s.buyers)}</TableCell>
                  <TableCell className="text-right">{percent(s.conversion)}</TableCell>
                  <TableCell className="text-right">{money(s.gmv)}</TableCell>
                  <TableCell className="text-right">{percent(s.gmv_share)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <ChartCard
        title="Vistas de producto y conversión por día"
        csv={daily.map((d) => ({ dia: d.day, impresiones: d.impressions, vistas: d.page_views, compradores: d.buyers, ordenes: d.orders, ventas: d.gmv, conversion: d.conversion }))}
        filename="embudo-por-dia"
      >
        {daily.length === 0 ? (
          <EmptyChart>{data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}</EmptyChart>
        ) : (
          <ComboChart
            data={daily}
            xKey="day"
            xFormat={shortDate}
            leftFormat={axisNumber}
            rightFormat={axisPct}
            series={[
              { key: 'page_views', label: 'Vistas de producto', type: 'bar', color: '#3b82f6', format: (v) => count(v) },
              { key: 'conv', label: 'Conversión', type: 'line', axis: 'right', color: '#f59e0b', format: (v) => percent(v) },
            ]}
          />
        )}
      </ChartCard>

      <Hint>
        Datos de la API de analytics de TikTok Shop (hasta 90 días atrás). Los compradores se suman por día, así que una
        persona que compró en dos días cuenta dos veces. El tráfico de la tienda es de todos los canales de TikTok, no
        solo de tus órdenes sincronizadas.
      </Hint>
    </div>
  )
}
