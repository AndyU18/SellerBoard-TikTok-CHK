import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, pct1 } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { type FeedView, walmartQuality } from '@/lib/walmartQuality'

import { CsvButton, PageTitle, SearchBox } from './walmartUi'

const UNKNOWN_TYPE = 'Sin tipo'
const DAY_LENGTH = 10 // "YYYY-MM-DD" dentro de una fecha ISO
const STACK = 'items'

/** Estado de Walmart (en mayúsculas) -> etiqueta y color de la insignia. */
const STATUS_PILL: Record<string, { label: string; className: string }> = {
  PROCESSED: { label: 'Procesada', className: PILL.green },
  ERROR: { label: 'Con error', className: PILL.red },
  FAILED: { label: 'Fallida', className: PILL.red },
  RECEIVED: { label: 'Recibida', className: PILL.blue },
  INPROGRESS: { label: 'En proceso', className: PILL.blue },
}

function StatusPill({ status }: { status: string | null }) {
  const key = (status ?? '').toUpperCase()
  const p = STATUS_PILL[key] ?? { label: status ?? 'Sin estado', className: PILL.slate }
  return <Pill label={p.label} className={p.className} />
}

const dateTime = (iso: string | null) => (iso ? iso.slice(0, 16).replace('T', ' ') : '—')
const shortDay = (v: string | number) => String(v).slice(5)

/** Cargas por día: total y las que traen fallos (el día se toma de la fecha ISO de la carga). */
function feedsPerDay(feeds: FeedView[]) {
  const byDay = new Map<string, { date: string; feeds: number; with_failures: number }>()
  for (const f of feeds) {
    if (!f.feed_date) continue
    const date = f.feed_date.slice(0, DAY_LENGTH)
    const cur = byDay.get(date) ?? { date, feeds: 0, with_failures: 0 }
    cur.feeds += 1
    if (f.items_failed > 0) cur.with_failures += 1
    byDay.set(date, cur)
  }
  return [...byDay.values()].sort((a, b) => a.date.localeCompare(b.date))
}

const DAY_SERIES: ComboSeries[] = [
  { key: 'with_failures', label: 'Con fallos', type: 'bar', color: COLOR.bad, format: count },
  { key: 'feeds', label: 'Cargas', type: 'line', color: COLOR.blue, format: count },
]

function FeedsTable({ feeds, loading, emptyText }: { feeds: FeedView[]; loading: boolean; emptyText: string }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Fecha</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead className="text-right">Recibidos</TableHead>
          <TableHead className="text-right">Correctos</TableHead>
          <TableHead className="text-right">Fallidos</TableHead>
          <TableHead className="text-right">En proceso</TableHead>
          <TableHead>ID de carga</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {feeds.length === 0 && (
          <TableRow>
            <TableCell colSpan={8} className="text-muted-foreground py-8 text-center">
              {loading ? 'Cargando…' : emptyText}
            </TableCell>
          </TableRow>
        )}
        {feeds.map((f) => (
          <TableRow key={f.feed_id} className={f.items_failed > 0 ? 'bg-red-50' : ''}>
            <TableCell className="whitespace-nowrap">{dateTime(f.feed_date)}</TableCell>
            <TableCell>{f.feed_type ?? UNKNOWN_TYPE}</TableCell>
            <TableCell>
              <StatusPill status={f.status} />
            </TableCell>
            <TableCell className="text-right">{count(f.items_received)}</TableCell>
            <TableCell className="text-right">{count(f.items_succeeded)}</TableCell>
            <TableCell className={`text-right ${f.items_failed > 0 ? 'font-medium text-red-700' : ''}`}>{count(f.items_failed)}</TableCell>
            <TableCell className="text-right">{count(f.items_processing)}</TableCell>
            <TableCell className="font-mono text-xs">{f.feed_id}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function WalmartFeeds() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const state = useAsync(() => walmartQuality.feeds(days), [days])
  const d = state.data
  const s = d?.summary
  const [search, setSearch] = useState('')
  const empty = (msg: string) => <EmptyChart>{state.loading ? 'Cargando…' : msg}</EmptyChart>

  const typeRows = useMemo<HBarRow[]>(
    () => (d?.by_type ?? []).map((t) => ({ id: t.feed_type, label: t.feed_type, succeeded: t.succeeded, failed: t.failed })),
    [d?.by_type],
  )
  const perDay = useMemo(() => feedsPerDay(d?.recent ?? []), [d?.recent])
  const recent = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (d?.recent ?? []).filter((f) => !needle || (f.feed_type ?? '').toLowerCase().includes(needle) || f.feed_id.toLowerCase().includes(needle))
  }, [d?.recent, search])
  const failing = d?.with_failures ?? []

  const csvRows = recent.map((f) => ({
    Fecha: f.feed_date,
    Tipo: f.feed_type,
    Estado: f.status,
    Recibidos: f.items_received,
    Correctos: f.items_succeeded,
    Fallidos: f.items_failed,
    'En proceso': f.items_processing,
    'Errores de datos': f.data_errors,
    'Errores de sistema': f.system_errors,
    'ID de carga': f.feed_id,
  }))
  const rate = s?.success_rate

  return (
    <div className="space-y-6">
      <PageTitle title="Estado de cargas (Feeds)" scope={scope} note={d ? `Cargas de los últimos ${d.days} días (período global)` : undefined} />

      <ErrorAlert message={state.error} />
      {state.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Tasa de éxito"
          value={pct1(rate)}
          hint="artículos correctos / recibidos"
          className={rate == null ? '' : rate >= 0.98 ? 'text-emerald-600' : rate >= 0.9 ? 'text-amber-600' : 'text-red-600'}
        />
        <Kpi title="Cargas" value={count(s?.feeds ?? 0)} hint="en el período" />
        <Kpi
          title="Cargas con fallos"
          value={count(s?.feeds_with_failures ?? 0)}
          hint={`${count(s?.items_failed ?? 0)} artículos fallidos`}
          className={(s?.feeds_with_failures ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi title="Artículos procesados" value={count(s?.items_succeeded ?? 0)} hint={`de ${count(s?.items_received ?? 0)} recibidos`} />
      </div>

      {failing.length > 0 && (
        <Callout tone="danger">
          {count(failing.length)} {failing.length === 1 ? 'carga trae' : 'cargas traen'} artículos fallidos. Revisa la lista de abajo: corrige los datos y vuelve a enviarlas.
        </Callout>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Artículos por tipo de carga"
          subtitle="Correctos y fallidos apilados por tipo de carga."
          filename="walmart-feeds-por-tipo"
          csv={(d?.by_type ?? []).map((t) => ({ Tipo: t.feed_type, Cargas: t.feeds, Recibidos: t.received, Correctos: t.succeeded, Fallidos: t.failed, 'Tasa de éxito': t.success_rate }))}
        >
          {typeRows.length === 0 ? (
            empty('Sin cargas en este período.')
          ) : (
            <HBarChart
              rows={typeRows}
              series={[
                { key: 'succeeded', label: 'Correctos', color: COLOR.good, stack: STACK },
                { key: 'failed', label: 'Fallidos', color: COLOR.bad, stack: STACK },
              ]}
              format={count}
              labelWidth={150}
              onRowClick={(row) => setSearch(row.id)}
            />
          )}
        </ChartCard>

        <ChartCard
          title="Cargas por día"
          subtitle="Línea: cargas enviadas. Barras: cargas con artículos fallidos."
          filename="walmart-feeds-por-dia"
          csv={perDay.map((r) => ({ Fecha: r.date, Cargas: r.feeds, 'Con fallos': r.with_failures }))}
        >
          {perDay.length === 0 ? empty('Sin cargas en este período.') : <ComboChart data={perDay} xKey="date" series={DAY_SERIES} xFormat={shortDay} leftFormat={count} height={280} />}
        </ChartCard>
      </div>

      <Card className={failing.length > 0 ? 'border-red-300' : ''}>
        <CardHeader>
          <CardTitle>Cargas con fallos</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <FeedsTable feeds={failing} loading={state.loading} emptyText="Ninguna carga con fallos en este período." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>Cargas recientes</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <SearchBox value={search} onChange={setSearch} placeholder="Buscar tipo o ID de carga" />
            <CsvButton filename="walmart-feeds-recientes.csv" rows={csvRows} />
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <FeedsTable feeds={recent} loading={state.loading} emptyText="Ninguna carga coincide o no hay cargas en este período." />
        </CardContent>
      </Card>

      <Hint>
        Una carga (feed) es un envío masivo de artículos, precios o inventario a Walmart. Se considera con fallos si algún artículo
        falló o el estado es de error. Se muestran hasta las 200 cargas más recientes; los totales de arriba cubren todo el período.
      </Hint>
    </div>
  )
}
