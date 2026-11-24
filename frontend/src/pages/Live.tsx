import { useMemo, useState } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { ErrorAlert } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { HeatGrid } from '@/components/charts/HeatGrid'
import { axisNumber, ChartCard, EmptyChart } from '@/components/charts/kit'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, type Filters } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { chartdataDinero, shortDate, weekKey, type LiveSessionPoint } from '@/lib/chartdata_dinero'
import { count, money, percent } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const WEEKDAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'))

/** Día de la semana (0 = lunes) y hora en la zona del navegador. */
const slot = (iso: string) => {
  const d = new Date(iso)
  return { dow: (d.getDay() + 6) % 7, hour: d.getHours() }
}
const localDay = (iso: string) => {
  const d = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const minutes = (v: number | null | undefined) => {
  if (v == null) return '—'
  const h = Math.floor(v / 60)
  return h > 0 ? `${h} h ${Math.round(v % 60)} min` : `${Math.round(v)} min`
}

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

export function Live() {
  const scope = useScope()
  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.live(filters), [filters])
  const all = useAsync(() => chartdataDinero.liveSessions(filters), [filters])
  const sm = data.data?.summary
  const [heatMetric, setHeatMetric] = useState<'gmv' | 'sessions'>('gmv')
  const [detail, setDetail] = useState<{ title: string; rows: LiveSessionPoint[] } | null>(null)
  const sessions = useMemo(() => all.data?.sessions ?? [], [all.data])

  // Por día; con más de 45 días distintos se agrupa por semana para que la serie se lea.
  const series = useMemo(() => {
    const days = new Set(sessions.map((x) => localDay(x.start_at)))
    const byWeek = days.size > 45
    const m = new Map<string, { bucket: string; gmv: number; customers: number; orders: number; sessions: number; rows: LiveSessionPoint[] }>()
    for (const x of sessions) {
      const day = localDay(x.start_at)
      const k = byWeek ? weekKey(day) : day
      const r = m.get(k) ?? { bucket: k, gmv: 0, customers: 0, orders: 0, sessions: 0, rows: [] }
      r.gmv += x.gmv
      r.customers += x.customers
      r.orders += x.orders
      r.sessions += 1
      r.rows.push(x)
      m.set(k, r)
    }
    return { byWeek, rows: [...m.values()].sort((a, b) => a.bucket.localeCompare(b.bucket)) }
  }, [sessions])

  const grid = useMemo(() => {
    const cells = WEEKDAYS.map(() => HOURS.map(() => ({ gmv: 0, sessions: 0, rows: [] as LiveSessionPoint[] })))
    for (const x of sessions) {
      const { dow, hour } = slot(x.start_at)
      const c = cells[dow][hour]
      c.gmv += x.gmv
      c.sessions += 1
      c.rows.push(x)
    }
    return cells
  }, [sessions])
  const bestSlots = useMemo(
    () =>
      grid
        .flatMap((row, d) => row.map((c, h) => ({ d, h, ...c })))
        .filter((c) => c.sessions > 0)
        .sort((a, b) => b.gmv - a.gmv)
        .slice(0, 3),
    [grid],
  )

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">LIVE shopping</h1>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="LIVE shopping" />

      <ErrorAlert message={data.error} />
      <ErrorAlert message={all.error} />

      {sm && sm.sessions > 0 && sm.sessions_with_sales < sm.sessions / 2 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>
            {count(sm.sessions - sm.sessions_with_sales)} de {count(sm.sessions)} transmisiones no vendieron nada.
          </strong>{' '}
          El LIVE aporta muy poco frente a los videos (ver Embudo): la mayoría de las sesiones son de creadores con
          audiencias pequeñas.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Ventas por LIVE"
          value={money(sm?.gmv ?? 0)}
          hint={`${count(sm?.orders ?? 0)} órdenes · ${count(sm?.customers ?? 0)} compradores`}
        />
        <Kpi
          title="Transmisiones"
          value={count(sm?.sessions ?? 0)}
          hint={`${count(sm?.sessions_with_sales ?? 0)} con ventas · duración prom. ${minutes(sm?.avg_duration_min)}`}
        />
        <Kpi
          title="Ventas por hora en vivo"
          value={money(sm?.gmv_per_hour)}
          hint={`${money(sm?.avg_gmv_per_session)} por transmisión en promedio`}
        />
        <Kpi
          title="Concentración top 5"
          value={percent(sm?.top5_share)}
          hint="de las ventas vienen de las 5 mejores sesiones"
        />
      </div>

      <ChartCard
        title="Ventas por transmisión LIVE en el tiempo"
        subtitle={`Barras = ventas, línea = compradores (TikTok no entrega espectadores). ${series.byWeek ? 'Agrupado por semana.' : 'Agrupado por día.'} Clic para ver las transmisiones.`}
        csv={series.rows.map((r) => ({ periodo: r.bucket, transmisiones: r.sessions, ventas: r.gmv, ordenes: r.orders, compradores: r.customers }))}
        filename="live-ventas-en-el-tiempo"
      >
        {series.rows.length === 0 ? (
          <EmptyChart>{all.loading ? 'Cargando…' : 'Sin transmisiones en este período.'}</EmptyChart>
        ) : (
          <ComboChart
            data={series.rows.map((r) => ({ bucket: r.bucket, gmv: r.gmv, customers: r.customers }))}
            xKey="bucket"
            xFormat={shortDate}
            rightFormat={axisNumber}
            onPointClick={(r) => {
              const row = series.rows.find((x) => x.bucket === r.bucket)
              if (row) setDetail({ title: `Transmisiones ${series.byWeek ? 'de la semana del' : 'del'} ${shortDate(row.bucket)}`, rows: row.rows })
            }}
            series={[
              { key: 'gmv', label: 'Ventas', type: 'bar', color: '#3b82f6' },
              { key: 'customers', label: 'Compradores', type: 'line', axis: 'right', color: '#f59e0b', format: (v) => count(v) },
            ]}
          />
        )}
      </ChartCard>

      <ChartCard
        title="Mejores franjas para transmitir"
        subtitle={`Día × hora de inicio (hora de tu navegador).${bestSlots.length ? ` Mejores: ${bestSlots.map((b) => `${WEEKDAYS[b.d]} ${HOURS[b.h]}h (${money(b.gmv)})`).join(' · ')}.` : ''} Clic en una celda para ver sus transmisiones.`}
        csv={grid.flatMap((row, d) => row.map((c, h) => ({ dia: WEEKDAYS[d], hora: h, transmisiones: c.sessions, ventas: c.gmv })))}
        filename="live-franjas"
        actions={
          <Tabs value={heatMetric} onValueChange={(v) => setHeatMetric(v as 'gmv' | 'sessions')}>
            <TabsList>
              <TabsTrigger value="gmv">Ventas</TabsTrigger>
              <TabsTrigger value="sessions">Transmisiones</TabsTrigger>
            </TabsList>
          </Tabs>
        }
      >
        {sessions.length === 0 ? (
          <EmptyChart>{all.loading ? 'Cargando…' : 'Sin transmisiones en este período.'}</EmptyChart>
        ) : (
          <HeatGrid
            rowLabels={WEEKDAYS}
            colLabels={HOURS}
            values={grid.map((row) => row.map((c) => (heatMetric === 'gmv' ? c.gmv : c.sessions)))}
            format={heatMetric === 'gmv' ? money : (v) => count(v)}
            unit={heatMetric === 'gmv' ? '' : 'transmisiones'}
            onCellClick={(d, h) => {
              const c = grid[d][h]
              if (c.rows.length) setDetail({ title: `Transmisiones del ${WEEKDAYS[d]} a las ${HOURS[h]}h`, rows: c.rows })
            }}
          />
        )}
      </ChartCard>

      {detail && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">{detail.title}</CardTitle>
            <button type="button" onClick={() => setDetail(null)} className="rounded border px-2 py-0.5 text-xs hover:bg-slate-50">
              Cerrar
            </button>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Creador</TableHead>
                  <TableHead>Inicio</TableHead>
                  <TableHead className="text-right">Duración</TableHead>
                  <TableHead className="text-right">Órdenes</TableHead>
                  <TableHead className="text-right">Compradores</TableHead>
                  <TableHead className="text-right">Ventas</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...detail.rows].sort((a, b) => b.gmv - a.gmv).slice(0, 25).map((x) => (
                  <TableRow key={x.external_id}>
                    <TableCell>@{x.username ?? '—'}</TableCell>
                    <TableCell className="text-xs">{x.start_at.slice(0, 16).replace('T', ' ')}</TableCell>
                    <TableCell className="text-right">{minutes(x.duration_min)}</TableCell>
                    <TableCell className="text-right">{count(x.orders)}</TableCell>
                    <TableCell className="text-right">{count(x.customers)}</TableCell>
                    <TableCell className="text-right font-medium">{money(x.gmv)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <Card>
          <CardHeader>
            <CardTitle>Mejores transmisiones</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Creador</TableHead>
                  <TableHead>Inicio</TableHead>
                  <TableHead className="text-right">Duración</TableHead>
                  <TableHead className="text-right">Órdenes</TableHead>
                  <TableHead className="text-right">Ventas</TableHead>
                  <TableHead className="text-right">Por hora</TableHead>
                  <TableHead className="text-right">Clic a orden</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data.data?.top_sessions ?? []).length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} className="text-muted-foreground py-10 text-center">
                      {data.loading ? 'Cargando…' : 'Sin transmisiones. Sincroniza TikTok Shop desde Importar.'}
                    </TableCell>
                  </TableRow>
                )}
                {(data.data?.top_sessions ?? []).map((s) => (
                  <TableRow key={s.external_id}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="font-medium">@{s.username ?? '—'}</span>
                        {s.title && <span className="text-muted-foreground max-w-48 truncate text-xs">{s.title}</span>}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">{s.start_at.slice(0, 16).replace('T', ' ')}</TableCell>
                    <TableCell className="text-right">{minutes(s.duration_min)}</TableCell>
                    <TableCell className="text-right">{count(s.orders)}</TableCell>
                    <TableCell className="text-right font-medium">{money(s.gmv)}</TableCell>
                    <TableCell className="text-right">{money(s.gmv_per_hour)}</TableCell>
                    <TableCell className="text-right">{percent(s.click_to_order_rate)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Creadores con más ventas en LIVE</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="divide-y text-sm">
              {(data.data?.by_creator ?? []).map((c) => (
                <li key={c.username} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="min-w-0 truncate">
                    @{c.username}
                    <span className="text-muted-foreground ml-2 text-xs">{count(c.sessions)} sesiones</span>
                  </span>
                  <span className="shrink-0 font-medium">{money(c.gmv)}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      <p className="text-muted-foreground max-w-3xl text-xs">
        Incluye las transmisiones de tus creadores afiliados. TikTok entrega hasta 600 sesiones por sincronización,
        ordenadas por ventas, y solo los últimos 90 días. No incluye espectadores ni gasto de promoción del LIVE
        (para eso hace falta autorizar TikTok Ads). Las horas en vivo suman la duración de todas las sesiones.
      </p>
    </div>
  )
}
