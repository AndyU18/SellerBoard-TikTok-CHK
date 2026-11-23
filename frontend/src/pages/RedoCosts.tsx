import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Callout, ErrorAlert, Hint, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import type { Channel } from '@/lib/api'
import { redo, type RedoCostBucket, type RedoOrderRow, type RedoOrigin } from '@/lib/redo'
import { useAsync } from '@/lib/useAsync'

const PERIODS = [
  { value: '30', label: '30 días' },
  { value: '90', label: '90 días' },
  { value: '180', label: '180 días' },
  { value: '365', label: '365 días' },
]

const ORIGIN_LABEL: Record<RedoOrigin, { label: string; pill: string }> = {
  tiktok_shop: { label: 'TikTok Shop', pill: PILL.green },
  walmart: { label: 'Walmart', pill: PILL.blue },
  tiktok_pendiente: { label: 'TikTok (sin sincronizar)', pill: PILL.amber },
  shopify: { label: 'Shopify (sin integrar)', pill: PILL.slate },
  otro: { label: 'Otro', pill: PILL.slate },
}

type SortKey = 'cost' | 'orders' | 'avg_cost' | 'cost_per_unit' | 'share_of_sales' | 'key'

function OrdersTable({ rows, empty }: { rows: RedoOrderRow[]; empty: string }) {
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
          <TableRow key={o.order}>
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

export function RedoCosts() {
  const [days, setDays] = useState('90')
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'cost', dir: -1 })
  const data = useAsync(() => redo.costs(Number(days)), [days])
  const d = data.data

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

  const maxOrders = Math.max(0, ...(d?.distribution ?? []).map((b) => b.orders))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Redo: costo por orden</h1>
        <Segmented label="Periodo" value={days} options={PERIODS} onChange={setDays} />
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

      <Card>
        <CardHeader>
          <CardTitle>Por canal de venta</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
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
              {(d?.by_channel.length ?? 0) === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground py-8 text-center">
                    {data.loading ? 'Cargando…' : 'Sin órdenes comparables con ventas en este periodo.'}
                  </TableCell>
                </TableRow>
              )}
              {(d?.by_channel ?? []).map((c) => (
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
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Por estado de destino</CardTitle>
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
                {states.map((s) => (
                  <TableRow key={s.key} className={pricey(s) ? 'bg-red-50' : ''}>
                    <TableCell>{s.key}</TableCell>
                    <TableCell className="text-right">{count(s.orders)}</TableCell>
                    <TableCell className="text-right">{money(s.cost)}</TableCell>
                    <TableCell className={`text-right ${pricey(s) ? 'font-medium text-red-700' : ''}`}>
                      {money(s.avg_cost)}
                    </TableCell>
                    <TableCell className="text-right">{pct1(s.share_of_sales)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Órdenes por rango de costo</CardTitle>
          </CardHeader>
          <CardContent className="h-80">
            {maxOrders === 0 ? (
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                {data.loading ? 'Cargando…' : 'Sin órdenes con costo en este periodo.'}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={d?.distribution}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="bucket" interval={0} tick={{ fontSize: 11 }} />
                  <YAxis allowDecimals={false} width={48} />
                  <Tooltip formatter={(v) => `${count(Number(v))} órdenes`} />
                  <Bar dataKey="orders" name="Órdenes" fill="#2563eb" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Las 25 órdenes más caras</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <OrdersTable rows={d?.top_orders ?? []} empty={data.loading ? 'Cargando…' : 'Sin órdenes en este periodo.'} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Órdenes re-etiquetadas</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <OrdersTable
            rows={d?.relabeled_orders ?? []}
            empty={data.loading ? 'Cargando…' : 'Ninguna orden tuvo más de una etiqueta.'}
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
