import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Callout, ErrorAlert, Hint, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, EmptyChart } from '@/components/charts/kit'
import { stateCode, UsTileMap } from '@/components/charts/UsTileMap'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CHANNEL_LABEL, type Channel } from '@/lib/api'
import { count, money } from '@/lib/format'
import { daysFromPreset, redo, type RedoCostBucket, type RedoOrderRow, type RedoOrigin } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const ORIGIN_LABEL: Record<RedoOrigin, { label: string; pill: string }> = {
  tiktok_shop: { label: 'TikTok Shop', pill: PILL.green },
  walmart: { label: 'Walmart', pill: PILL.blue },
  tiktok_pendiente: { label: 'TikTok (sin sincronizar)', pill: PILL.amber },
  shopify: { label: 'Shopify (sin integrar)', pill: PILL.slate },
  otro: { label: 'Otro', pill: PILL.slate },
}

/** Rangos de costo que entrega la API (mismos textos que backend/app/redo_analytics.py BUCKETS). */
const BUCKET_RANGE: Record<string, [number, number]> = {
  'Menos de $8': [0, 8],
  '$8 a $12': [8, 12],
  '$12 a $16': [12, 16],
  '$16 a $25': [16, 25],
  '$25 o más': [25, Infinity],
}

type SortKey = 'cost' | 'orders' | 'avg_cost' | 'cost_per_unit' | 'share_of_sales' | 'key'
type Metric = 'avg_cost' | 'cost' | 'share_of_sales'

const METRICS: { value: Metric; label: string }[] = [
  { value: 'avg_cost', label: 'Costo promedio por orden' },
  { value: 'cost', label: 'Costo total' },
  { value: 'share_of_sales', label: '% de la venta' },
]

const channelName = (key: string) => CHANNEL_LABEL[key as Channel] ?? key
const pctAxis = (v: number) => `${(v * 100).toFixed(1)}%`

function OrdersTable({ rows, empty, focus }: { rows: RedoOrderRow[]; empty: string; focus: string | null }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Orden</TableHead>
          <TableHead>Origen</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead>Fecha</TableHead>
          <TableHead className="text-right">Etiquetas</TableHead>
          <TableHead className="text-right">Corrección</TableHead>
          <TableHead className="text-right">Venta neta</TableHead>
          <TableHead className="text-right">Costo</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={8} className="text-muted-foreground py-8 text-center">
              {empty}
            </TableCell>
          </TableRow>
        )}
        {rows.map((o) => (
          <TableRow key={o.order} className={focus === o.order ? 'bg-blue-50' : ''}>
            <TableCell className="font-mono text-xs">{o.order}</TableCell>
            <TableCell>
              <Pill label={ORIGIN_LABEL[o.channel]?.label ?? o.channel} className={ORIGIN_LABEL[o.channel]?.pill ?? PILL.slate} />
            </TableCell>
            <TableCell>{o.state ?? '—'}</TableCell>
            <TableCell>{o.date}</TableCell>
            <TableCell className="text-right">
              {count(o.labels)}
              {o.voids > 0 && <span className="text-muted-foreground"> ({o.voids} anul.)</span>}
            </TableCell>
            <TableCell className={`text-right ${o.surcharge > 0 ? 'text-amber-600' : ''}`}>{money(o.surcharge)}</TableCell>
            <TableCell className="text-right">{money(o.net_sales)}</TableCell>
            <TableCell className="text-right font-medium">{money(o.cost)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Chip que muestra un filtro activo y permite quitarlo. */
function ActiveFilter({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <button type="button" onClick={onClear} className="rounded-full border bg-blue-50 px-3 py-1 text-xs hover:bg-blue-100">
      {label} ✕
    </button>
  )
}

export function RedoCosts() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'cost', dir: -1 })
  const [metric, setMetric] = useState<Metric>('avg_cost')
  const [selState, setSelState] = useState<string | null>(null)
  const [bucket, setBucket] = useState<string | null>(null)
  const [focusOrder, setFocusOrder] = useState<string | null>(null)
  const data = useAsync(() => redo.costs(days), [days])
  const d = data.data
  const empty = (msg: string) => <EmptyChart>{data.loading ? 'Cargando…' : msg}</EmptyChart>

  const states = useMemo(() => {
    const rows = [...(d?.by_state ?? [])]
    rows.sort((a, b) => {
      const av = a[sort.key] ?? -Infinity
      const bv = b[sort.key] ?? -Infinity
      if (typeof av === 'string' && typeof bv === 'string') return av.localeCompare(bv) * sort.dir
      return ((av as number) - (bv as number)) * sort.dir
    })
    return rows
  }, [d?.by_state, sort])

  // Un estado es "caro" si su costo promedio por orden supera 1.25 veces el promedio general.
  const avgAll = useMemo(() => {
    const rows = d?.by_state ?? []
    const orders = rows.reduce((s, r) => s + r.orders, 0)
    return orders ? rows.reduce((s, r) => s + r.cost, 0) / orders : null
  }, [d?.by_state])
  const pricey = (r: RedoCostBucket) => avgAll != null && r.orders >= 5 && r.avg_cost != null && r.avg_cost > avgAll * 1.25

  const toggle = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: key === 'key' ? 1 : -1 }))
  const head = (key: SortKey, label: string, right = true) => (
    <TableHead className={right ? 'text-right' : ''}>
      <button type="button" onClick={() => toggle(key)} className="hover:underline">
        {label}
        {sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
      </button>
    </TableHead>
  )

  const metricFmt = metric === 'share_of_sales' ? pctAxis : money
  const mapData = (d?.by_state ?? [])
    .filter((s) => s[metric] != null)
    .map((s) => ({ state: s.key, value: s[metric] as number, extra: `${s.orders} órdenes` }))

  const pickState = (name: string) => {
    const next = selState === name ? null : name
    setSelState(next)
    if (next) {
      const row = (d?.by_state ?? []).find((s) => stateCode(s.key) === stateCode(next))
      if (row) document.getElementById(`redo-state-${row.key}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }
  const selCode = stateCode(selState)

  const range = bucket ? BUCKET_RANGE[bucket] : null
  const inBucket = (o: RedoOrderRow) => !range || (o.cost >= range[0] && o.cost < range[1])
  const topOrders = (d?.top_orders ?? []).filter(inBucket)
  const relabeled = (d?.relabeled_orders ?? []).filter(inBucket)

  const bubbles = useMemo<BubblePoint[]>(() => {
    const seen = new Map<string, RedoOrderRow>()
    for (const o of [...(d?.top_orders ?? []), ...(d?.relabeled_orders ?? [])]) seen.set(o.order, o)
    return [...seen.values()].map((o) => ({
      id: o.order,
      label: `Orden ${o.order}`,
      x: o.cost,
      y: o.surcharge,
      z: Math.max(o.net_sales ?? 0, 1),
      group: ORIGIN_LABEL[o.channel]?.label ?? o.channel,
    }))
  }, [d?.top_orders, d?.relabeled_orders])

  const channels = (d?.by_channel ?? []).map((c) => ({ ...c, name: channelName(c.key) }))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Redo: costo por orden</h1>
        {d && (
          <p className="text-muted-foreground text-xs">
            Del {d.window.start} al {d.window.end} (período global: {days} días)
          </p>
        )}
      </div>

      <ErrorAlert message={data.error} />
      {data.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      {(d?.unmatched_orders ?? 0) > 0 && (
        <Callout tone="warning">
          <strong>{count(d?.unmatched_orders)} órdenes con etiqueta no están en las ventas de la app</strong> (TikTok
          sin sincronizar, Shopify sin integrar u otras), así que no aparecen en las tablas por canal y por estado.
          Las {count(d?.matched_orders)} restantes sí se pudieron comparar con su venta.
        </Callout>
      )}

      <ChartCard
        title="Por canal de venta"
        subtitle="Barras: costo total. Línea (eje derecho): costo promedio por orden."
        filename="redo-por-canal"
        csv={channels.map((c) => ({
          Canal: c.name,
          Ordenes: c.orders,
          Costo: c.cost,
          'Costo promedio': c.avg_cost,
          'Costo por unidad': c.cost_per_unit,
          '% de la venta neta': c.share_of_sales == null ? null : Math.round(c.share_of_sales * 10000) / 100,
        }))}
      >
        {channels.length === 0 ? (
          empty('Sin órdenes comparables con ventas en este período.')
        ) : (
          <div className="space-y-4">
            <ComboChart
              data={channels}
              xKey="name"
              height={240}
              rightFormat={(v) => `$${v.toFixed(0)}`}
              series={[
                { key: 'cost', label: 'Costo total', type: 'bar', color: '#2563eb', format: money },
                { key: 'avg_cost', label: 'Costo promedio por orden', type: 'line', axis: 'right', color: '#dc2626', format: money },
              ]}
            />
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Canal</TableHead>
                    <TableHead className="text-right">Órdenes</TableHead>
                    <TableHead className="text-right">Costo</TableHead>
                    <TableHead className="text-right">Costo promedio</TableHead>
                    <TableHead className="text-right">Costo por unidad</TableHead>
                    <TableHead className="text-right">% de la venta neta</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {channels.map((c) => (
                    <TableRow key={c.key}>
                      <TableCell>
                        <ChannelBadge channel={c.key as Channel} />
                      </TableCell>
                      <TableCell className="text-right">{count(c.orders)}</TableCell>
                      <TableCell className="text-right">{money(c.cost)}</TableCell>
                      <TableCell className="text-right">{money(c.avg_cost)}</TableCell>
                      <TableCell className="text-right">{money(c.cost_per_unit)}</TableCell>
                      <TableCell className="text-right">{pct1(c.share_of_sales)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
      </ChartCard>

      <ChartCard
        title="Mapa de EE. UU. por estado de destino"
        subtitle="Elige la métrica; pasa el cursor para el detalle y haz clic en un estado para resaltar su fila en la tabla."
        filename="redo-mapa-estados"
        csv={(d?.by_state ?? []).map((s) => ({
          Estado: s.key,
          Ordenes: s.orders,
          Costo: s.cost,
          'Costo promedio': s.avg_cost,
          '% de la venta': s.share_of_sales == null ? null : Math.round(s.share_of_sales * 10000) / 100,
        }))}
        actions={<Segmented label="Métrica del mapa" value={metric} options={METRICS} onChange={setMetric} />}
      >
        {mapData.length === 0 ? (
          empty('Sin datos por estado.')
        ) : (
          <UsTileMap
            data={mapData}
            format={metricFmt}
            selected={selState}
            onStateClick={pickState}
            describe={(name, v, extra) =>
              v == null ? `${name}: sin datos` : `${name}: ${metricFmt(v)} (${METRICS.find((m) => m.value === metric)?.label.toLowerCase()}) · ${extra ?? ''}`
            }
          />
        )}
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <CardTitle>Por estado de destino</CardTitle>
            {selState && <ActiveFilter label={`Resaltado: ${selState}`} onClear={() => setSelState(null)} />}
          </CardHeader>
          <CardContent className="max-h-[32rem] overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {head('key', 'Estado', false)}
                  {head('orders', 'Órdenes')}
                  {head('cost', 'Costo')}
                  {head('avg_cost', 'Promedio')}
                  {head('share_of_sales', '% venta')}
                </TableRow>
              </TableHeader>
              <TableBody>
                {states.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin datos.'}
                    </TableCell>
                  </TableRow>
                )}
                {states.map((s) => {
                  const on = selCode != null && stateCode(s.key) === selCode
                  return (
                    <TableRow
                      key={s.key}
                      id={`redo-state-${s.key}`}
                      className={on ? 'bg-blue-100' : pricey(s) ? 'bg-red-50' : ''}
                      onClick={() => setSelState(on ? null : s.key)}
                    >
                      <TableCell className={on ? 'font-semibold' : ''}>{s.key}</TableCell>
                      <TableCell className="text-right">{count(s.orders)}</TableCell>
                      <TableCell className="text-right">{money(s.cost)}</TableCell>
                      <TableCell className={`text-right ${pricey(s) ? 'font-medium text-red-700' : ''}`}>
                        {money(s.avg_cost)}
                      </TableCell>
                      <TableCell className="text-right">{pct1(s.share_of_sales)}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <ChartCard
          title="Órdenes por rango de costo"
          subtitle="Haz clic en una barra para filtrar las tablas de órdenes de abajo a ese rango."
          filename="redo-distribucion"
          csv={(d?.distribution ?? []).map((b) => ({ Rango: b.bucket, Ordenes: b.orders }))}
        >
          {Math.max(0, ...(d?.distribution ?? []).map((b) => b.orders)) === 0 ? (
            empty('Sin órdenes con costo en este período.')
          ) : (
            <ComboChart
              data={d?.distribution ?? []}
              xKey="bucket"
              leftFormat={(v) => String(v)}
              series={[{ key: 'orders', label: 'Órdenes', type: 'bar', color: '#2563eb', format: (v) => `${count(v)} órdenes` }]}
              onPointClick={(row) => setBucket((b) => (b === row.bucket ? null : String(row.bucket)))}
            />
          )}
          {bucket && (
            <div className="mt-2">
              <ActiveFilter label={`Rango: ${bucket}`} onClear={() => setBucket(null)} />
            </div>
          )}
        </ChartCard>
      </div>

      <ChartCard
        title="Costo por orden frente a corrección del carrier"
        subtitle="Cada burbuja es una orden (las 25 más caras y las re-etiquetadas). Tamaño = venta neta; color = origen. Clic para resaltarla en las tablas."
        filename="redo-dispersion"
        csv={bubbles.map((b) => ({ Orden: b.id, Origen: b.group, Costo: b.x, 'Correccion del carrier': b.y, 'Venta neta': b.z }))}
      >
        {bubbles.length === 0 ? (
          empty('Sin órdenes en este período.')
        ) : (
          <>
            <BubbleChart
              points={bubbles}
              xLabel="Costo de la orden"
              yLabel="Corrección del carrier"
              zLabel="Venta neta"
              xFormat={money}
              yFormat={money}
              zFormat={money}
              onPointClick={(p) => setFocusOrder((cur) => (cur === p.id ? null : p.id))}
            />
            {focusOrder && (
              <div className="mt-2">
                <ActiveFilter label={`Orden resaltada: ${focusOrder}`} onClear={() => setFocusOrder(null)} />
              </div>
            )}
          </>
        )}
      </ChartCard>

      <Card>
        <CardHeader>
          <CardTitle>Las 25 órdenes más caras{bucket ? ` · ${bucket}` : ''}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <OrdersTable rows={topOrders} focus={focusOrder} empty={data.loading ? 'Cargando…' : 'Sin órdenes en este periodo o rango.'} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Órdenes re-etiquetadas{bucket ? ` · ${bucket}` : ''}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <OrdersTable
            rows={relabeled}
            focus={focusOrder}
            empty={data.loading ? 'Cargando…' : 'Ninguna orden re-etiquetada en este periodo o rango.'}
          />
        </CardContent>
      </Card>

      <Hint>
        El costo de una orden suma sus etiquetas y correcciones del carrier y resta las anulaciones. Los estados en rojo
        cuestan más de 1.25 veces el promedio (con al menos 5 órdenes). Re-etiquetada significa que se compró más de una
        etiqueta para la misma orden.
      </Hint>
    </div>
  )
}
