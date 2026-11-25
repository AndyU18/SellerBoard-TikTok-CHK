import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Kpi, Pill, PILL, Segmented } from '@/components/analytics/ui'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { ChartCard } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { type ShowcaseProduct, tiktokCreatorAccount } from '@/lib/tiktokCreatorAccount'
import { useAsync } from '@/lib/useAsync'

import { CsvButton, PageTitle, SearchBox } from '../walmart/walmartUi'
import { matchesSearch } from './tiktokCatalogFormat'
import { ProductImage } from './tiktokCatalogUi'
import { NO_VALUE } from './tiktokFormat'
import { NUM_CELL } from './tiktokMoneyFormat'
import { EmptyRows } from './tiktokMoneyUi'

const SAME_LIST_NOTICE =
  'La API de TikTok devuelve la misma lista de productos para la vitrina y para el LIVE: aquí no se puede distinguir cuáles se muestran en cada uno.'
const NO_PRODUCTS = 'Todavía no hay productos en tu vitrina.'
const NO_MATCH = 'Ningún producto coincide con la búsqueda.'

type ViewMode = 'gallery' | 'table'
const VIEW_OPTIONS: { value: ViewMode; label: string }[] = [
  { value: 'gallery', label: 'Galería' },
  { value: 'table', label: 'Tabla' },
]

const UNKNOWN_STATUS = 'UNKNOWN'
const REVIEW_LABEL: Record<string, { label: string; className: string }> = {
  APPROVED: { label: 'Aprobado', className: PILL.green },
  PASSED: { label: 'Aprobado', className: PILL.green },
  IN_REVIEW: { label: 'En revisión', className: PILL.amber },
  PENDING: { label: 'En revisión', className: PILL.amber },
  REJECTED: { label: 'Rechazado', className: PILL.red },
  FAILED: { label: 'Rechazado', className: PILL.red },
  [UNKNOWN_STATUS]: { label: 'Sin estado', className: PILL.slate },
}
const INVENTORY_LABEL: Record<string, string> = {
  IN_STOCK: 'Con inventario',
  OUT_OF_STOCK: 'Agotado',
  [UNKNOWN_STATUS]: 'Sin dato',
}

const reviewInfo = (status: string | null) => {
  const key = (status ?? UNKNOWN_STATUS).toUpperCase()
  return REVIEW_LABEL[key] ?? { label: status ?? 'Sin estado', className: PILL.slate }
}
const inventoryLabel = (status: string | null) => INVENTORY_LABEL[(status ?? UNKNOWN_STATUS).toUpperCase()] ?? status ?? NO_VALUE

/** "$10.00" o "$10.00 – $12.00"; usa el precio con descuento si existe y el original si no. */
function priceText(p: ShowcaseProduct): string {
  const [min, max] = p.discount_price_min != null ? [p.discount_price_min, p.discount_price_max] : [p.original_price_min, p.original_price_max]
  if (min == null) return NO_VALUE
  return max != null && max !== min ? `${money(min)} – ${money(max)}` : money(min)
}

const csvRow = (p: ShowcaseProduct) => ({
  'ID de producto': p.product_id,
  Producto: p.title ?? '',
  Tienda: p.shop_name ?? '',
  Precio: priceText(p),
  Moneda: p.currency ?? '',
  Revisión: reviewInfo(p.review_status).label,
  Inventario: inventoryLabel(p.inventory_status),
  Oculto: p.is_hidden ? 'Sí' : 'No',
})

function ProductCard({ product }: { product: ShowcaseProduct }) {
  const review = reviewInfo(product.review_status)
  return (
    <Card>
      <CardContent className="flex gap-3 pt-4">
        <ProductImage src={product.image_url} title={product.title} className="size-16" />
        <div className="min-w-0 flex-1 space-y-1 text-sm">
          <p className="line-clamp-2 font-medium">{product.title ?? NO_VALUE}</p>
          <p className="text-muted-foreground text-xs">{product.shop_name ?? NO_VALUE}</p>
          <p className="font-semibold tabular-nums">{priceText(product)}</p>
          <div className="flex flex-wrap gap-1.5">
            <Pill label={review.label} className={review.className} />
            <Pill label={inventoryLabel(product.inventory_status)} className={PILL.slate} />
            {product.is_hidden && <Pill label="Oculto" className={PILL.orange} />}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function ProductsTable({ rows }: { rows: ShowcaseProduct[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Producto</TableHead>
          <TableHead>Tienda</TableHead>
          <TableHead className="text-right">Precio</TableHead>
          <TableHead>Revisión</TableHead>
          <TableHead>Inventario</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((p) => {
          const review = reviewInfo(p.review_status)
          return (
            <TableRow key={p.product_id}>
              <TableCell>
                <div className="flex items-center gap-2">
                  <ProductImage src={p.image_url} title={p.title} className="size-10" />
                  <span className="line-clamp-2 min-w-48">{p.title ?? NO_VALUE}</span>
                </div>
              </TableCell>
              <TableCell>{p.shop_name ?? NO_VALUE}</TableCell>
              <TableCell className={NUM_CELL}>{priceText(p)}</TableCell>
              <TableCell>
                <Pill label={review.label} className={review.className} />
              </TableCell>
              <TableCell>{inventoryLabel(p.inventory_status)}</TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

/** Vitrina propia de la cuenta de creador: productos con su estado de revisión e inventario. Solo lectura. */
export function TikTokCreatorShowcase() {
  const description = useScopeDescription()
  const report = useAsync(() => tiktokCreatorAccount.showcase(), [])
  const data = report.data

  const [search, setSearch] = useState('')
  const [view, setView] = useState<ViewMode>('gallery')

  const all = useMemo(() => data?.products ?? [], [data])
  const rows = useMemo(() => all.filter((p) => matchesSearch(search, p.title, p.shop_name, p.product_id)), [all, search])
  const reviewSlices = useMemo<DonutSlice[]>(
    () => Object.entries(data?.summary.by_review_status ?? {}).map(([status, value]) => ({ id: status, label: reviewInfo(status).label, value })),
    [data],
  )

  const summary = data?.summary
  const approved = summary?.by_review_status.APPROVED ?? 0

  return (
    <div className="space-y-6">
      <PageTitle title="Vitrina" scope={description} note="Los productos de tu vitrina de creador y su estado de revisión. Solo lectura." />
      <ErrorAlert message={report.error} />
      <Callout tone="info">{SAME_LIST_NOTICE}</Callout>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Productos en la vitrina" value={summary ? count(summary.products) : '—'} />
        <Kpi title="Aprobados" value={summary ? count(approved) : '—'} hint="pasaron la revisión de TikTok" className={approved > 0 ? 'text-emerald-600' : ''} />
        <Kpi title="Ocultos" value={summary ? count(summary.hidden) : '—'} hint="no se muestran a los compradores" />
        <Kpi
          title="Sin inventario"
          value={summary ? count(summary.by_inventory_status.OUT_OF_STOCK ?? 0) : '—'}
          hint="agotados según TikTok"
          className={(summary?.by_inventory_status.OUT_OF_STOCK ?? 0) > 0 ? 'text-amber-600' : ''}
        />
      </div>

      {reviewSlices.length > 0 && (
        <ChartCard
          title="Estado de revisión"
          subtitle="Cuántos productos de la vitrina hay en cada estado de revisión de TikTok."
          filename="tiktok-creator-vitrina-revision"
          csv={reviewSlices.map((s) => ({ Estado: s.label, Productos: s.value }))}
        >
          <Donut slices={reviewSlices} format={count} centerLabel="productos" />
        </ChartCard>
      )}

      <Card>
        <CardContent className="space-y-3 overflow-x-auto pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Segmented label="Vista de la vitrina" value={view} options={VIEW_OPTIONS} onChange={setView} />
            <div className="flex items-center gap-2">
              <SearchBox value={search} onChange={setSearch} placeholder="Buscar por producto o tienda" />
              <CsvButton filename="tiktok-creator-vitrina.csv" rows={rows.map(csvRow)} />
            </div>
          </div>
          {rows.length === 0 ? (
            <EmptyRows loading={report.loading} message={all.length ? NO_MATCH : NO_PRODUCTS} />
          ) : view === 'gallery' ? (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {rows.map((p) => (
                <ProductCard key={p.product_id} product={p} />
              ))}
            </div>
          ) : (
            <ProductsTable rows={rows} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
