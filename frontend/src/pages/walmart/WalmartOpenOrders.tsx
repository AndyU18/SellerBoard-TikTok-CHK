// "Pedidos por surtir" de Walmart: datos en vivo (GET /api/walmart/open-orders), sin datos personales.
import { RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Kpi, PILL, Pill, Segmented } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, type CsvRow } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { HOURS_BUCKETS, hoursBucket, walmartOrders, type HoursBucket, type OpenOrder } from '@/lib/walmartOrders'
import { useAsync } from '@/lib/useAsync'
import { CsvButton, PageTitle, SearchBox, SortHead } from '@/pages/walmart/walmartUi'
import { compareValues, nextSort, type SortState } from '@/pages/walmart/walmartSort'

type BucketFilter = HoursBucket | 'all'
type SortKey = 'order_id' | 'ship_by' | 'hours_left' | 'status' | 'units' | 'sales' | 'lines'

const TEXT_KEYS: readonly SortKey[] = ['order_id', 'status']
const PAGE_ROWS = 100
const HOURS_PER_DAY = 24
const URGENT_HOURS = 6
const DATE_FORMAT = new Intl.DateTimeFormat('es-MX', { dateStyle: 'medium', timeStyle: 'short' })
const TIME_FORMAT = new Intl.DateTimeFormat('es-MX', { timeStyle: 'medium' })

function formatDate(iso: string | null): string {
  return iso ? DATE_FORMAT.format(new Date(iso)) : '—'
}

/** "Vencido hace 5.2 h", "Faltan 12.0 h", "Faltan 3.5 d". */
function formatHoursLeft(hours: number | null): string {
  if (hours == null) return 'Sin plazo'
  const abs = Math.abs(hours)
  const text = abs >= HOURS_PER_DAY * 2 ? `${(abs / HOURS_PER_DAY).toFixed(1)} d` : `${abs.toFixed(1)} h`
  return hours < 0 ? `Vencido hace ${text}` : `Faltan ${text}`
}

function hoursPill(hours: number | null): string {
  if (hours == null) return PILL.slate
  if (hours < 0) return PILL.red
  if (hours < URGENT_HOURS) return PILL.orange
  if (hours < HOURS_PER_DAY) return PILL.amber
  return PILL.green
}

function sortValue(o: OpenOrder, key: SortKey): string | number | null {
  return o[key]
}

function csvRows(rows: OpenOrder[]): CsvRow[] {
  return rows.map((o) => ({
    'Compra': o.order_id,
    'Plazo de envío': o.ship_by,
    'Horas que faltan': o.hours_left,
    'Estado': o.status,
    'Unidades': o.units,
    'Ventas': o.sales,
    'Líneas': o.lines,
  }))
}

function FreshnessBadge({ cached, asOf, ttl }: { cached: boolean; asOf: string; ttl: number }) {
  return (
    <p className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
      <Pill label={cached ? 'De caché' : 'En vivo'} className={cached ? PILL.amber : PILL.green} />
      <span>
        Consultado a las {TIME_FORMAT.format(new Date(asOf))}.
        {cached && ` Walmart se consulta como máximo cada ${ttl} s; pulsa Actualizar pasado ese tiempo para ver cambios.`}
      </span>
    </p>
  )
}

export function WalmartOpenOrders() {
  const scope = useScopeDescription()
  const [reloads, setReloads] = useState(0)
  const data = useAsync(() => walmartOrders.openOrders(), [reloads])
  const [bucket, setBucket] = useState<BucketFilter>('all')
  const [text, setText] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'hours_left', dir: 1 })
  const [shown, setShown] = useState(PAGE_ROWS)

  const d = data.data
  const orders = useMemo(() => d?.orders ?? [], [d])

  const bucketCounts = useMemo(() => {
    const counts = new Map<HoursBucket, number>()
    for (const o of orders) {
      const b = hoursBucket(o.hours_left)
      counts.set(b, (counts.get(b) ?? 0) + 1)
    }
    return counts
  }, [orders])

  const chartRows = HOURS_BUCKETS.filter((b) => b.key !== 'none' || bucketCounts.has('none')).map((b) => ({
    key: b.key,
    label: b.label,
    pedidos: bucketCounts.get(b.key) ?? 0,
  }))

  const rows = useMemo(() => {
    const needle = text.trim().toLowerCase()
    const filtered = orders.filter(
      (o) =>
        (bucket === 'all' || hoursBucket(o.hours_left) === bucket) &&
        (!needle || o.order_id.toLowerCase().includes(needle) || (o.status ?? '').toLowerCase().includes(needle)),
    )
    return [...filtered].sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [orders, bucket, text, sort])

  const pickBucket = (b: BucketFilter) => {
    setBucket(b)
    setShown(PAGE_ROWS)
  }
  const bucketOptions: { value: BucketFilter; label: string; count: number }[] = [
    { value: 'all', label: 'Todos', count: orders.length },
    ...HOURS_BUCKETS.filter((b) => bucketCounts.has(b.key)).map((b) => ({
      value: b.key,
      label: b.label,
      count: bucketCounts.get(b.key) ?? 0,
    })),
  ]
  const s = d?.summary
  const atRisk = s ? s.sales_overdue + s.sales_due_soon : 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageTitle
          title="Pedidos por surtir"
          scope={scope}
          note="Datos en vivo de Walmart: no dependen del período elegido. No se muestran datos personales."
        />
        <button
          type="button"
          onClick={() => setReloads((n) => n + 1)}
          disabled={data.loading}
          className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none disabled:opacity-50"
        >
          <RefreshCw className={`size-4 ${data.loading ? 'animate-spin' : ''}`} aria-hidden /> Actualizar
        </button>
      </div>

      {data.error && <ErrorAlert message={data.error} />}
      {data.loading && !d && <p className="text-muted-foreground text-sm">Consultando a Walmart…</p>}

      {d && s && (
        <>
          <FreshnessBadge cached={d.cached} asOf={d.as_of} ttl={d.cache_ttl_seconds} />

          {orders.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center">
                <p className="font-medium">No hay pedidos por surtir</p>
                <p className="text-muted-foreground text-sm">Todo lo que Walmart tenía pendiente ya se envió.</p>
              </CardContent>
            </Card>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Kpi
                  title="Vencidos"
                  value={count(s.overdue)}
                  className={s.overdue > 0 ? 'text-red-600' : ''}
                  hint={`${money(s.sales_overdue)} en ventas`}
                />
                <Kpi
                  title={`Por vencer en ${s.due_soon_window_hours} h`}
                  value={count(s.due_soon)}
                  className={s.due_soon > 0 ? 'text-amber-600' : ''}
                  hint={`${money(s.sales_due_soon)} en ventas`}
                />
                <Kpi title="Ventas en riesgo" value={money(atRisk)} hint="Vencidos más por vencer" />
                <Kpi
                  title="Por surtir en total"
                  value={count(s.open_orders)}
                  hint={`${count(s.units)} unidades · ${money(s.sales)}`}
                />
              </div>

              {s.no_deadline > 0 && (
                <Callout tone="info">
                  {count(s.no_deadline)} pedido(s) no traen plazo de envío; no cuentan como vencidos ni por vencer.
                </Callout>
              )}

              <ChartCard
                title="Pedidos por horas restantes"
                subtitle="Haz clic en una barra para filtrar la tabla."
                csv={chartRows.map((r) => ({ Cubeta: r.label, Pedidos: r.pedidos }))}
                filename="walmart-pedidos-por-horas"
              >
                <ComboChart
                  data={chartRows}
                  xKey="label"
                  height={240}
                  leftFormat={count}
                  series={[{ key: 'pedidos', label: 'Pedidos', type: 'bar', color: COLOR.amber, format: count }]}
                  onPointClick={(row) => pickBucket(row.key as HoursBucket)}
                />
              </ChartCard>

              <Card>
                <CardContent className="space-y-3 pt-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Segmented label="Horas restantes" value={bucket} options={bucketOptions} onChange={pickBucket} />
                    <div className="flex items-center gap-2">
                      <SearchBox value={text} onChange={setText} placeholder="Buscar compra o estado" />
                      <CsvButton filename="walmart-pedidos-por-surtir.csv" rows={csvRows(rows)} />
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <SortHead sortKey="order_id" label="Compra" sort={sort} right={false} onSort={(k) => setSort((c) => nextSort(c, k, TEXT_KEYS))} />
                          <SortHead sortKey="ship_by" label="Plazo de envío" sort={sort} right={false} onSort={(k) => setSort((c) => nextSort(c, k, ['ship_by']))} />
                          <SortHead sortKey="hours_left" label="Tiempo" sort={sort} onSort={(k) => setSort((c) => nextSort(c, k, ['hours_left']))} />
                          <SortHead sortKey="status" label="Estado" sort={sort} right={false} onSort={(k) => setSort((c) => nextSort(c, k, TEXT_KEYS))} />
                          <SortHead sortKey="units" label="Unidades" sort={sort} onSort={(k) => setSort((c) => nextSort(c, k))} />
                          <SortHead sortKey="sales" label="Ventas" sort={sort} onSort={(k) => setSort((c) => nextSort(c, k))} />
                          <SortHead sortKey="lines" label="Líneas" sort={sort} onSort={(k) => setSort((c) => nextSort(c, k))} />
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {rows.slice(0, shown).map((o) => (
                          <TableRow key={o.order_id}>
                            <TableCell className="font-mono text-xs">{o.order_id}</TableCell>
                            <TableCell className="whitespace-nowrap">{formatDate(o.ship_by)}</TableCell>
                            <TableCell className="text-right">
                              <Pill label={formatHoursLeft(o.hours_left)} className={hoursPill(o.hours_left)} />
                            </TableCell>
                            <TableCell>{o.status ?? '—'}</TableCell>
                            <TableCell className="text-right tabular-nums">{count(o.units)}</TableCell>
                            <TableCell className="text-right tabular-nums">{money(o.sales)}</TableCell>
                            <TableCell className="text-right tabular-nums">{count(o.lines)}</TableCell>
                          </TableRow>
                        ))}
                        {rows.length === 0 && (
                          <TableRow>
                            <TableCell colSpan={7} className="text-muted-foreground text-center">
                              Ningún pedido coincide con el filtro.
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </div>
                  {rows.length > shown && (
                    <button
                      type="button"
                      onClick={() => setShown((n) => n + PAGE_ROWS)}
                      className="rounded-md border px-3 py-1.5 text-sm hover:bg-slate-100"
                    >
                      Mostrar más ({rows.length - shown} restantes)
                    </button>
                  )}
                </CardContent>
              </Card>
            </>
          )}
        </>
      )}
    </div>
  )
}
