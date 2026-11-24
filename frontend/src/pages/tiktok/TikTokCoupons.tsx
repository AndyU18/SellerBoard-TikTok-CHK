import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, pct1, Segmented } from '@/components/analytics/ui'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { axisPct, ChartCard, COLOR, EmptyChart, type CsvRow } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokPromos, type Coupon } from '@/lib/tiktokPromos'
import { useAsync } from '@/lib/useAsync'
import { compareValues } from '@/pages/walmart/walmartSort'
import { CsvButton, nextSort, SearchBox, SortHead, type SortState } from '@/pages/walmart/walmartUi'

import { dateRange, humanize, NO_VALUE, toMs } from './tiktokFormat'

const TOP_RANKING = 12
const LABEL_MAX = 34
const ACTIVE_STATUSES: readonly string[] = ['ONGOING', 'ACTIVE']

type Breakdown = 'discount_type' | 'status'
const BREAKDOWNS: { value: Breakdown; label: string }[] = [
  { value: 'discount_type', label: 'Tipo de descuento' },
  { value: 'status', label: 'Estado' },
]

type SortKey = 'title' | 'type' | 'discount' | 'valid' | 'claimed' | 'redeemed' | 'rate' | 'products'
const TEXT_KEYS: readonly SortKey[] = ['title', 'type', 'discount']
const INITIAL_SORT: SortState<SortKey> = { key: 'redeemed', dir: -1 }

const clip = (text: string) => (text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text)
const nameOf = (c: Coupon) => c.title ?? c.promo_code ?? c.coupon_id
const isActive = (c: Coupon) => ACTIVE_STATUSES.includes(c.status ?? '')

function sortValue(c: Coupon, key: SortKey): string | number | null {
  switch (key) {
    case 'title':
      return nameOf(c).toLowerCase()
    case 'type':
      return c.discount_type
    case 'discount':
      return c.discount
    case 'valid':
      return toMs(c.valid_until)
    case 'claimed':
      return c.claimed
    case 'redeemed':
      return c.redeemed
    case 'rate':
      return c.redemption_rate
    case 'products':
      return c.product_count
  }
}

function csvRows(rows: Coupon[]): CsvRow[] {
  return rows.map((c) => ({
    cupon: nameOf(c),
    codigo: c.promo_code,
    estado: humanize(c.status),
    tipo_descuento: humanize(c.discount_type),
    descuento: c.discount,
    gasto_minimo: c.min_spend,
    vigente_desde: c.valid_from,
    vigente_hasta: c.valid_until,
    reclamados: c.claimed,
    canjeados: c.redeemed,
    tasa_de_canje: c.redemption_rate == null ? '' : c.redemption_rate.toFixed(4),
    productos: c.product_count,
  }))
}

function toSlices(counts: Record<string, number>): DonutSlice[] {
  return Object.entries(counts).map(([id, value]) => ({ id, label: humanize(id), value }))
}

function CouponsTable({ rows }: { rows: Coupon[] }) {
  const [sort, setSort] = useState<SortState<SortKey>>(INITIAL_SORT)
  const [search, setSearch] = useState('')
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows
      .filter((c) => !q || `${nameOf(c)} ${c.promo_code ?? ''} ${c.discount}`.toLowerCase().includes(q))
      .sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [rows, search, sort])

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">Cupones ({count(shown.length)})</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={search} onChange={setSearch} placeholder="Buscar cupón o código" />
          <CsvButton filename="tiktok-cupones.csv" rows={csvRows(shown)} />
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead sortKey="title" label="Cupón" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="type" label="Tipo" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="discount" label="Descuento" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="valid" label="Vigencia" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="claimed" label="Reclamados" sort={sort} onSort={onSort} />
              <SortHead sortKey="redeemed" label="Canjeados" sort={sort} onSort={onSort} />
              <SortHead sortKey="rate" label="Tasa de canje" sort={sort} onSort={onSort} />
              <SortHead sortKey="products" label="Productos" sort={sort} onSort={onSort} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((c) => (
              <TableRow key={c.coupon_id}>
                <TableCell className="max-w-xs font-medium">
                  <div className="truncate" title={nameOf(c)}>
                    {nameOf(c)}
                  </div>
                  <div className="text-muted-foreground text-xs">{humanize(c.status)}</div>
                </TableCell>
                <TableCell>{humanize(c.discount_type)}</TableCell>
                <TableCell>{c.discount}</TableCell>
                <TableCell className="text-xs whitespace-nowrap">{dateRange(c.valid_from, c.valid_until)}</TableCell>
                <TableCell className="text-right">{count(c.claimed)}</TableCell>
                <TableCell className="text-right">{count(c.redeemed)}</TableCell>
                <TableCell className="text-right font-medium">{pct1(c.redemption_rate)}</TableCell>
                <TableCell className="text-right">{c.product_count > 0 ? count(c.product_count) : NO_VALUE}</TableCell>
              </TableRow>
            ))}
            {shown.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-muted-foreground text-center">
                  Ningún cupón coincide con la búsqueda.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

/** Cupones de TikTok Shop: cuántos se reclaman y canjean, y cómo se reparten por tipo y estado. */
export function TikTokCoupons() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const data = useAsync(() => tiktokPromos.coupons(days), [days])
  const report = data.data
  const [breakdown, setBreakdown] = useState<Breakdown>('discount_type')

  const coupons = useMemo(() => report?.coupons ?? [], [report])
  const claimed = useMemo(() => coupons.reduce((a, c) => a + (c.claimed ?? 0), 0), [coupons])
  const redeemed = report?.summary.redeemed ?? 0
  const overallRate = claimed > 0 ? redeemed / claimed : null
  const activeCount = useMemo(() => coupons.filter(isActive).length, [coupons])

  const ranking = useMemo<HBarRow[]>(
    () =>
      coupons
        .filter((c) => c.redemption_rate != null)
        .sort((a, b) => (b.redemption_rate ?? 0) - (a.redemption_rate ?? 0))
        .slice(0, TOP_RANKING)
        .map((c) => ({ id: c.coupon_id, label: clip(nameOf(c)), rate: c.redemption_rate })),
    [coupons],
  )

  const slices = useMemo(
    () => (report ? toSlices(breakdown === 'status' ? report.summary.by_status : report.summary.by_discount_type) : []),
    [report, breakdown],
  )

  const reportedTotal = report?.summary.reported_total ?? null
  const loading = data.loading && !report
  const placeholder = loading ? 'Cargando…' : NO_VALUE

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Cupones de TikTok Shop</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
        <Hint>Tasa de canje = cupones canjeados entre cupones reclamados por los compradores.</Hint>
      </header>

      <ErrorAlert message={data.error} />

      <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Cupones activos" value={report ? count(activeCount) : placeholder} hint={report ? `${count(report.summary.coupons)} en el período` : undefined} />
        <Kpi title="Reclamados" value={report ? count(claimed) : placeholder} />
        <Kpi title="Canjes" value={report ? count(redeemed) : placeholder} />
        <Kpi title="Tasa de canje" value={report ? pct1(overallRate) : placeholder} hint={report ? 'Canjeados / reclamados' : undefined} />
      </section>

      {report && reportedTotal != null && reportedTotal > report.summary.coupons && (
        <Callout tone="warning">
          El listado devuelve {count(report.summary.coupons)} cupones, pero TikTok informa {count(reportedTotal)}: faltan {count(reportedTotal - report.summary.coupons)} que la API no entrega.
        </Callout>
      )}

      {loading && <EmptyChart>Cargando…</EmptyChart>}
      {report && coupons.length === 0 && <EmptyChart>No hay cupones en este período.</EmptyChart>}

      {report && coupons.length > 0 && (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Tasa de canje por cupón"
              subtitle={`Los ${TOP_RANKING} cupones con mayor tasa de canje.`}
              csv={ranking.map((r) => ({ cupon: r.label, tasa_de_canje: r.rate == null ? '' : Number(r.rate).toFixed(4) }))}
              filename="tiktok-cupones-tasa-de-canje"
            >
              {ranking.length ? (
                <HBarChart rows={ranking} series={[{ key: 'rate', label: 'Tasa de canje', color: COLOR.blue }]} format={axisPct} labelWidth={170} />
              ) : (
                <EmptyChart>Ningún cupón tiene reclamos todavía.</EmptyChart>
              )}
            </ChartCard>

            <ChartCard
              title="Cupones por categoría"
              subtitle="Cantidad de cupones."
              csv={slices.map((s) => ({ categoria: s.label, cupones: s.value }))}
              filename={`tiktok-cupones-por-${breakdown === 'status' ? 'estado' : 'tipo'}`}
              actions={<Segmented label="Agrupar por" value={breakdown} options={BREAKDOWNS} onChange={setBreakdown} />}
            >
              <Donut slices={slices} format={count} centerLabel="cupones" />
            </ChartCard>
          </div>

          <CouponsTable rows={coupons} />
        </>
      )}
    </div>
  )
}
