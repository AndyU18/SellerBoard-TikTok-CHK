import { useMemo, useState } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { ErrorAlert } from '@/components/analytics/ui'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { ComboChart } from '@/components/charts/ComboChart'
import { HeatTable, type HeatRow } from '@/components/charts/HeatTable'
import { axisNumber, ChartCard, EmptyChart } from '@/components/charts/kit'
import { api, type Filters } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { chartdataDinero, shortDate } from '@/lib/chartdata_dinero'
import { count, money, percent, tone } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const TYPE_LABEL: Record<string, string> = {
  VIDEO: 'Video',
  LIVE: 'LIVE',
  SHOP: 'Tienda del creador',
  LINKSHARE: 'Enlace compartido',
}

type Sort = 'gmv' | 'profit'

function Kpi({ title, value, hint, className = '' }: { title: string; value: string; hint?: string; className?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold ${className}`}>{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

export function Creatives() {
  const [type, setType] = useState<string>('all')
  const [sort, setSort] = useState<Sort>('gmv')
  const [metric, setMetric] = useState<'gmv' | 'orders'>('gmv')
  const [focus, setFocus] = useState<string | null>(null)
  const scope = useScope()
  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.creatives(filters), [filters])
  const weekly = useAsync(() => chartdataDinero.creativeWeekly(filters, 15), [filters])

  const sm = data.data?.summary
  const rows = useMemo(
    () =>
      (data.data?.contents ?? [])
        .filter((c) => type === 'all' || c.content_type === type)
        .filter((c) => !focus || `${c.content_type}-${c.content_id}` === focus)
        .sort((a, b) => b[sort] - a[sort]),
    [data.data, type, sort, focus],
  )
  const name = (c: { creator: string | null; content_type: string; content_id: string }) =>
    `${c.creator ? `@${c.creator} ` : ''}${TYPE_LABEL[c.content_type] ?? c.content_type} …${c.content_id.slice(-6)}`

  // Mejores contenidos: ventas en barras y vistas en línea (eje derecho).
  const top = useMemo(
    () =>
      [...(data.data?.contents ?? [])]
        .sort((a, b) => b.gmv - a.gmv)
        .slice(0, 12)
        .map((c) => ({ key: `${c.content_type}-${c.content_id}`, name: name(c), gmv: c.gmv, orders: c.orders, views: c.views, profit: c.profit })),
    [data.data],
  )

  const heat = useMemo<{ cols: string[]; rows: HeatRow[] }>(() => {
    const w = weekly.data
    if (!w) return { cols: [], rows: [] }
    return {
      cols: w.weeks.map(shortDate),
      rows: w.rows.map((r) => ({
        id: `${r.content_type}-${r.content_id}`,
        label: name({ creator: r.creator, content_type: r.content_type, content_id: r.content_id }),
        values: w.weeks.map((wk) => r.weeks[wk]?.[metric] ?? 0),
      })),
    }
  }, [weekly.data, metric])

  const points = useMemo<BubblePoint[]>(
    () =>
      (data.data?.contents ?? [])
        .filter((c) => c.views != null)
        .map((c) => ({
          id: `${c.content_type}-${c.content_id}`,
          label: name(c),
          x: c.views ?? 0,
          y: c.gmv,
          z: Math.max(1, c.orders),
          group: TYPE_LABEL[c.content_type] ?? c.content_type,
        })),
    [data.data],
  )

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Creativos y videos virales</h1>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="Creativos y videos virales" />

      <ErrorAlert message={data.error} />
      <ErrorAlert message={weekly.error} />

      {sm && sm.orders_pending > 0 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>{count(sm.orders_pending)} órdenes de afiliado aún no se liquidan.</strong> Su utilidad no se puede
          calcular todavía, así que la utilidad de los videos recientes aparece baja. Las ventas sí están completas.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Ventas por creadores"
          value={money(sm?.gmv ?? 0)}
          hint={`${count(sm?.orders ?? 0)} órdenes · ${count(sm?.contents ?? 0)} contenidos · ${count(sm?.creators ?? 0)} creadores`}
        />
        <Kpi
          title="Utilidad liquidada"
          value={money(sm?.profit ?? 0)}
          hint={`comisiones de afiliado ya descontadas (${money(sm?.commission ?? 0)})`}
          className={tone(sm?.profit)}
        />
        <Kpi
          title="Concentración top 10"
          value={percent(sm?.top10_share)}
          hint="de las ventas viene de los 10 mejores contenidos"
        />
        <Kpi
          title="Contenidos con pérdida"
          value={count(sm?.losing_contents ?? 0)}
          hint="utilidad negativa en lo ya liquidado"
          className={(sm?.losing_contents ?? 0) > 0 ? 'text-red-600' : ''}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Contenidos con más ventas"
          subtitle="Barras = ventas (base de comisión); línea = vistas de los últimos 60 días (eje derecho, solo videos). Clic para ver su fila."
          csv={top.map((t) => ({ contenido: t.name, ventas: t.gmv, ordenes: t.orders, vistas: t.views, utilidad: t.profit }))}
          filename="creativos-top-ventas"
        >
          {top.length === 0 ? (
            <EmptyChart>{data.loading ? 'Cargando…' : 'Sin ventas de creadores.'}</EmptyChart>
          ) : (
            <ComboChart
              data={top}
              xKey="name"
              xFormat={(v) => String(v).replace(/^@\S+ /, '')}
              rightFormat={axisNumber}
              onPointClick={(r) => setFocus(String(r.key))}
              series={[
                { key: 'gmv', label: 'Ventas', type: 'bar', color: '#3b82f6' },
                { key: 'views', label: 'Vistas', type: 'line', axis: 'right', color: '#f59e0b', format: (v) => count(v) },
              ]}
            />
          )}
        </ChartCard>

        <ChartCard
          title="Vistas vs ventas"
          subtitle="Cada burbuja es un video con vistas registradas; tamaño = órdenes. Arriba a la izquierda convierte mucho con poco alcance."
          csv={points.map((p) => ({ contenido: p.label, vistas: p.x, ventas: p.y, ordenes: p.z, tipo: p.group }))}
          filename="creativos-vistas-vs-ventas"
        >
          <BubbleChart
            points={points}
            xLabel="Vistas"
            yLabel="Ventas"
            zLabel="Órdenes"
            xFormat={axisNumber}
            yFormat={money}
            zFormat={(v) => count(v)}
            onPointClick={(p) => setFocus(p.id)}
          />
        </ChartCard>
      </div>

      <ChartCard
        title="Videos por semana"
        subtitle="Mapa de calor de los 15 contenidos con más ventas, por semana de la orden. El color compara cada fila contra sí misma. Las vistas no se guardan por semana, por eso no aparecen aquí."
        csv={heat.rows.map((r) => Object.fromEntries([['contenido', r.label], ...heat.cols.map((c, i) => [c, r.values[i]])]))}
        filename="creativos-por-semana"
        actions={
          <Tabs value={metric} onValueChange={(v) => setMetric(v as 'gmv' | 'orders')}>
            <TabsList>
              <TabsTrigger value="gmv">Ventas</TabsTrigger>
              <TabsTrigger value="orders">Órdenes</TabsTrigger>
            </TabsList>
          </Tabs>
        }
      >
        {heat.rows.length === 0 ? (
          <EmptyChart>{weekly.loading ? 'Cargando…' : 'Sin datos semanales.'}</EmptyChart>
        ) : (
          <HeatTable
            columns={heat.cols}
            rows={heat.rows}
            format={metric === 'gmv' ? money : (v) => count(v)}
            firstColumn="Contenido"
            onRowClick={(r) => setFocus(r.id)}
          />
        )}
      </ChartCard>

      {focus && (
        <button type="button" onClick={() => setFocus(null)} className="rounded border px-2 py-1 text-xs hover:bg-slate-50">
          Mostrando un solo contenido en la tabla · quitar filtro
        </button>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Por tipo de contenido</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tipo</TableHead>
                <TableHead className="text-right">Contenidos</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">Utilidad</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data.data?.by_type ?? []).map((t) => (
                <TableRow key={t.content_type}>
                  <TableCell>{TYPE_LABEL[t.content_type] ?? t.content_type}</TableCell>
                  <TableCell className="text-right">{count(t.contents)}</TableCell>
                  <TableCell className="text-right">{count(t.orders)}</TableCell>
                  <TableCell className="text-right">{money(t.gmv)}</TableCell>
                  <TableCell className={`text-right ${tone(t.profit)}`}>{money(t.profit)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-4">
        <Tabs value={type} onValueChange={setType}>
          <TabsList>
            <TabsTrigger value="all">Todos</TabsTrigger>
            <TabsTrigger value="VIDEO">Videos</TabsTrigger>
            <TabsTrigger value="LIVE">LIVE</TabsTrigger>
          </TabsList>
        </Tabs>
        <Tabs value={sort} onValueChange={(v) => setSort(v as Sort)}>
          <TabsList>
            <TabsTrigger value="gmv">Ordenar por ventas</TabsTrigger>
            <TabsTrigger value="profit">Ordenar por utilidad</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Contenido</TableHead>
                <TableHead>Creador</TableHead>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Vistas</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">Comisión</TableHead>
                <TableHead className="text-right">Utilidad</TableHead>
                <TableHead className="text-right">Margen</TableHead>
                <TableHead>Periodo</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin ventas de creadores. Sincroniza TikTok Shop desde Importar.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((c) => (
                <TableRow key={`${c.content_type}-${c.content_id}`}>
                  <TableCell>
                    <div className="flex flex-col">
                      {c.url ? (
                        <a href={c.url} target="_blank" rel="noreferrer" className="font-medium text-blue-700 hover:underline">
                          Video …{c.content_id.slice(-6)}
                        </a>
                      ) : (
                        <span className="font-medium">
                          {TYPE_LABEL[c.content_type] ?? c.content_type} …{c.content_id.slice(-6)}
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    @{c.creator ?? '—'}
                    {c.sampled && (
                      <span className="ml-2 rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-800" title="Recibió una muestra tuya">
                        muestra
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-xs">{c.top_sku ?? '—'}</TableCell>
                  <TableCell className="text-right">
                    {c.views == null ? (
                      '—'
                    ) : (
                      <>
                        {count(c.views)}
                        <span className="text-muted-foreground block text-xs">CTR {percent(c.ctr)}</span>
                      </>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {count(c.orders)}
                    {c.orders_pending > 0 && (
                      <span className="text-muted-foreground text-xs" title="Órdenes aún sin liquidar">
                        {' '}
                        ({c.orders_pending} sin liquidar)
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{money(c.gmv)}</TableCell>
                  <TableCell className="text-right">{money(c.commission)}</TableCell>
                  <TableCell className={`text-right font-medium ${tone(c.profit)}`}>{money(c.profit)}</TableCell>
                  <TableCell className={`text-right ${tone(c.margin)}`}>{percent(c.margin)}</TableCell>
                  <TableCell className="text-muted-foreground text-xs">
                    {c.first_order} → {c.last_order}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <p className="text-muted-foreground max-w-3xl text-xs">
        Cada venta de afiliado trae el video, LIVE o enlace que la originó. Si una orden viene de varios contenidos, su
        utilidad se reparte según la base de comisión de cada uno. Ventas = base de comisión de TikTok. Las vistas y el
        CTR son de los últimos 60 días y solo de los 300 videos con más ventas. No incluye gasto en anuncios (Spark
        Ads): para eso hace falta autorizar TikTok Ads.
      </p>
    </div>
  )
}
