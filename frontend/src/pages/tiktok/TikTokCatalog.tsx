import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, Pill, PILL, Segmented } from '@/components/analytics/ui'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { type CatalogProduct, tiktokCatalog } from '@/lib/tiktokCatalog'
import { useAsync } from '@/lib/useAsync'

import { compareValues, CsvButton, nextSort, PageTitle, SearchBox, SortHead, type SortState } from '../walmart/walmartUi'
import { clip, matchesSearch } from './tiktokCatalogFormat'
import { Fact, ProductImage, SidePanel } from './tiktokCatalogUi'
import { humanize, NO_VALUE, shortDate } from './tiktokFormat'

type ViewMode = 'gallery' | 'table'
type SortKey = 'title' | 'status' | 'audit_status' | 'listing_quality_tier' | 'min_price' | 'gmv_30d' | 'settled_sales'
type Filters = { status: string; quality: string; audit: string }

const ALL = 'all'
/** Así cuenta el backend los productos sin dato de estado, calidad o auditoría. */
const MISSING = '—'
const TEXT_KEYS: readonly SortKey[] = ['title', 'status', 'audit_status', 'listing_quality_tier']
const NO_FILTERS: Filters = { status: ALL, quality: ALL, audit: ALL }
const TITLE_MAX = 60

const VIEW_OPTIONS: { value: ViewMode; label: string }[] = [
  { value: 'gallery', label: 'Galería' },
  { value: 'table', label: 'Tabla' },
]

type Badge = { label: string; pill: string }
const STATUS_BADGE: Record<string, Badge> = {
  ACTIVATE: { label: 'Activo', pill: PILL.green },
  DRAFT: { label: 'Borrador', pill: PILL.slate },
  PENDING: { label: 'En revisión', pill: PILL.amber },
  FAILED: { label: 'Fallido', pill: PILL.red },
  SELLER_DEACTIVATED: { label: 'Desactivado por ti', pill: PILL.orange },
  PLATFORM_DEACTIVATED: { label: 'Desactivado por TikTok', pill: PILL.red },
  FREEZE: { label: 'Congelado', pill: PILL.red },
  DELETED: { label: 'Eliminado', pill: PILL.red },
}
const AUDIT_BADGE: Record<string, Badge> = {
  APPROVED: { label: 'Aprobado', pill: PILL.green },
  PENDING: { label: 'Pendiente', pill: PILL.amber },
  FAILED: { label: 'Rechazado', pill: PILL.red },
}
const QUALITY_BADGE: Record<string, Badge & { color: string }> = {
  GOOD: { label: 'Buena', pill: PILL.green, color: COLOR.good },
  FAIR: { label: 'Regular', pill: PILL.amber, color: COLOR.amber },
  POOR: { label: 'Baja', pill: PILL.red, color: COLOR.bad },
}
const UNKNOWN_BADGE: Badge = { label: NO_VALUE, pill: PILL.slate }

const badgeOf = (map: Record<string, Badge>, code: string | null): Badge =>
  code ? (map[code] ?? { label: humanize(code), pill: PILL.slate }) : UNKNOWN_BADGE
const labelOf = (map: Record<string, Badge>, code: string) => (code === MISSING ? 'Sin dato' : badgeOf(map, code).label)
const bucket = (v: string | null) => v ?? MISSING

function ProductBadges({ p }: { p: CatalogProduct }) {
  const status = badgeOf(STATUS_BADGE, p.status)
  const audit = badgeOf(AUDIT_BADGE, p.audit_status)
  const quality = badgeOf(QUALITY_BADGE, p.listing_quality_tier)
  return (
    <div className="flex flex-wrap gap-1">
      <Pill label={status.label} className={status.pill} />
      <Pill label={`Auditoría: ${audit.label}`} className={audit.pill} />
      <Pill label={`Calidad: ${quality.label}`} className={quality.pill} />
    </div>
  )
}

function priceText(p: CatalogProduct): string {
  if (p.min_price == null) return NO_VALUE
  return p.max_price != null && p.max_price !== p.min_price ? `${money(p.min_price)} – ${money(p.max_price)}` : money(p.min_price)
}

function FilterSelect({ label, value, options, onChange }: { label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground text-xs">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="h-9 rounded-md border bg-transparent px-2 text-sm">
        <option value={ALL}>Todos</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function SummaryCard({ title, counts, map }: { title: string; counts: Record<string, number>; map: Record<string, Badge> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1])
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1.5 text-sm">
          {entries.map(([code, n]) => (
            <li key={code} className="flex items-center justify-between gap-2">
              <Pill label={labelOf(map, code)} className={code === MISSING ? PILL.slate : badgeOf(map, code).pill} />
              <span className="tabular-nums">{count(n)}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function GalleryCard({ p, onOpen }: { p: CatalogProduct; onOpen: (p: CatalogProduct) => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(p)}
        className={`flex h-full w-full flex-col gap-2 rounded-lg border p-2 text-left hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none ${p.problem ? 'border-red-300 bg-red-50/40' : ''}`}
      >
        <ProductImage src={p.image_url ?? p.thumb_url} title={p.title} className="aspect-square w-full" />
        <span className="line-clamp-2 text-sm font-medium">{p.title ?? p.product_id}</span>
        <ProductBadges p={p} />
        <span className="text-muted-foreground mt-auto text-xs">
          {priceText(p)} · GMV {money(p.gmv_30d)}
        </span>
      </button>
    </li>
  )
}

function ProblemBlock({ rows, total, days, onOpen }: { rows: CatalogProduct[]; total: number; days: number; onOpen: (p: CatalogProduct) => void }) {
  if (rows.length === 0) return null
  return (
    <Card className="border-red-300 bg-red-50/50">
      <CardHeader>
        <CardTitle className="text-red-800">Productos con problemas ({count(rows.length)})</CardTitle>
        <p className="text-sm text-red-800">
          Rechazados en la auditoría o no activos. Ventas liquidadas que se pierden en {days} días: <strong>{money(total)}</strong>.
        </p>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((p) => (
            <li key={p.product_id}>
              <button
                type="button"
                onClick={() => onOpen(p)}
                className="flex w-full items-center gap-3 rounded-md border bg-white p-2 text-left hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
              >
                <ProductImage src={p.thumb_url ?? p.image_url} title={p.title} className="size-12" />
                <span className="min-w-0 flex-1 space-y-1">
                  <span className="block truncate text-sm font-medium">{p.title ?? p.product_id}</span>
                  <ProductBadges p={p} />
                </span>
                <span className="text-right text-sm font-semibold tabular-nums text-red-700">{money(p.settled_sales)}</span>
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function ProductPanel({ p, days, onClose }: { p: CatalogProduct; days: number; onClose: () => void }) {
  return (
    <SidePanel title={p.title ?? p.product_id} subtitle={`ID ${p.product_id}`} onClose={onClose}>
      <ProductImage src={p.image_url ?? p.thumb_url} title={p.title} className="aspect-square w-full" />
      <ProductBadges p={p} />
      {p.audit_reasons.length > 0 && (
        <Callout tone="danger">
          <p className="mb-1 font-medium">Motivos de la auditoría</p>
          <ul className="list-disc pl-5">
            {p.audit_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </Callout>
      )}
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <Fact label="SKU (precio)" value={`${count(p.sku_count)} · ${priceText(p)}`} />
        <Fact label="Marca" value={p.brand ?? NO_VALUE} />
        <Fact label="GMV (última foto)" value={money(p.gmv_30d)} />
        <Fact label={`Ventas liquidadas (${days} d)`} value={money(p.settled_sales)} />
        <Fact label="Imágenes" value={count(p.image_count)} />
        <Fact label="A la venta" value={p.is_not_for_sale == null ? NO_VALUE : p.is_not_for_sale ? 'No' : 'Sí'} />
        <Fact label="Creado" value={shortDate(p.created_at)} />
        <Fact label="Actualizado" value={shortDate(p.updated_at)} />
        <Fact label="Categorías" value={p.categories.join(' › ') || NO_VALUE} />
        <Fact label="Regiones de venta" value={p.sales_regions.join(', ') || NO_VALUE} />
      </dl>
    </SidePanel>
  )
}

export function TikTokCatalog() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const state = useAsync(() => tiktokCatalog.catalog(days), [days])
  const d = state.data
  const s = d?.summary ?? null
  const products = useMemo(() => d?.products ?? [], [d?.products])

  const [view, setView] = useState<ViewMode>('gallery')
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'gmv_30d', dir: -1 })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = [...products, ...(d?.problem_products ?? [])].find((p) => p.product_id === selectedId) ?? null
  const open = (p: CatalogProduct) => setSelectedId(p.product_id)
  const setFilter = (key: keyof Filters, value: string) => setFilters((cur) => ({ ...cur, [key]: cur[key] === value ? ALL : value }))

  const rows = useMemo(() => {
    const list = products.filter(
      (p) =>
        (filters.status === ALL || bucket(p.status) === filters.status) &&
        (filters.quality === ALL || bucket(p.listing_quality_tier) === filters.quality) &&
        (filters.audit === ALL || bucket(p.audit_status) === filters.audit) &&
        matchesSearch(search, p.title, p.product_id, p.brand),
    )
    return list.sort((a, b) => compareValues(a[sort.key], b[sort.key], sort.dir))
  }, [products, filters, search, sort])
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))

  const statusSlices = useMemo<DonutSlice[]>(
    () => Object.entries(s?.by_status ?? {}).map(([code, n]) => ({ id: code, label: labelOf(STATUS_BADGE, code), value: n })),
    [s?.by_status],
  )
  const qualityRows = useMemo<HBarRow[]>(
    () => Object.entries(s?.by_quality ?? {}).map(([code, n]) => ({ id: code, label: labelOf(QUALITY_BADGE, code), products: n })),
    [s?.by_quality],
  )
  const options = (counts: Record<string, number> | undefined, map: Record<string, Badge>) =>
    Object.keys(counts ?? {}).map((code) => ({ value: code, label: `${labelOf(map, code)} (${count(counts?.[code])})` }))
  const filtered = filters.status !== ALL || filters.quality !== ALL || filters.audit !== ALL || search.trim() !== ''

  const csvRows = rows.map((p) => ({
    Producto: p.title,
    ID: p.product_id,
    Estado: labelOf(STATUS_BADGE, bucket(p.status)),
    Auditoría: labelOf(AUDIT_BADGE, bucket(p.audit_status)),
    Calidad: labelOf(QUALITY_BADGE, bucket(p.listing_quality_tier)),
    Marca: p.brand,
    SKU: p.sku_count,
    'Precio mínimo': p.min_price,
    'Precio máximo': p.max_price,
    'GMV última foto': p.gmv_30d,
    'Ventas liquidadas': p.settled_sales,
    Imagen: p.image_url,
  }))

  return (
    <div className="space-y-6">
      <PageTitle title="Catálogo de productos" scope={scope} note={s ? `Ventas liquidadas de los últimos ${s.sales_days} días` : undefined} />

      <ErrorAlert message={state.error} />
      {state.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}
      {d && !d.available && <Callout tone="info">Todavía no hay productos de TikTok Shop sincronizados. Aparecerán tras la próxima sincronización.</Callout>}

      {d && s && d.available && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi title="Productos" value={count(s.products)} />
            <Kpi title="Con problemas" value={count(s.problems)} hint="rechazados o no activos" className={s.problems > 0 ? 'text-red-600' : ''} />
            <Kpi title="Ventas que se pierden" value={money(s.problem_settled_sales)} hint={`liquidadas en ${s.sales_days} días`} className={s.problem_settled_sales > 0 ? 'text-red-600' : ''} />
            <Kpi title="Sin imagen" value={count(s.without_image)} className={s.without_image > 0 ? 'text-amber-600' : ''} />
          </div>

          <ProblemBlock rows={d.problem_products} total={s.problem_settled_sales} days={s.sales_days} onOpen={open} />

          <div className="grid gap-4 md:grid-cols-3">
            <SummaryCard title="Por estado" counts={s.by_status} map={STATUS_BADGE} />
            <SummaryCard title="Por calidad del listado" counts={s.by_quality} map={QUALITY_BADGE} />
            <SummaryCard title="Por auditoría" counts={s.by_audit} map={AUDIT_BADGE} />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Productos por estado"
              subtitle="Un clic en una porción filtra el catálogo."
              filename="tiktok-catalogo-estado"
              csv={statusSlices.map((x) => ({ Estado: x.label, Productos: x.value }))}
            >
              <Donut slices={statusSlices} format={count} centerLabel="productos" onSliceClick={(x) => setFilter('status', x.id)} />
            </ChartCard>
            <ChartCard
              title="Productos por nivel de calidad"
              subtitle="Un clic en una barra filtra el catálogo; otro clic quita el filtro."
              filename="tiktok-catalogo-calidad"
              csv={qualityRows.map((r) => ({ Calidad: r.label, Productos: Number(r.products) }))}
            >
              <HBarChart
                rows={qualityRows}
                series={[{ key: 'products', label: 'Productos' }]}
                format={count}
                labelWidth={90}
                rowColor={(row) => QUALITY_BADGE[row.id]?.color}
                selectedId={filters.quality === ALL ? null : filters.quality}
                onRowClick={(row) => setFilter('quality', row.id)}
              />
            </ChartCard>
          </div>

          <Card>
            <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
              <CardTitle>Productos ({count(rows.length)})</CardTitle>
              <div className="flex flex-wrap items-end gap-2">
                <FilterSelect label="Estado" value={filters.status} options={options(s.by_status, STATUS_BADGE)} onChange={(v) => setFilters((c) => ({ ...c, status: v }))} />
                <FilterSelect label="Calidad" value={filters.quality} options={options(s.by_quality, QUALITY_BADGE)} onChange={(v) => setFilters((c) => ({ ...c, quality: v }))} />
                <FilterSelect label="Auditoría" value={filters.audit} options={options(s.by_audit, AUDIT_BADGE)} onChange={(v) => setFilters((c) => ({ ...c, audit: v }))} />
                <SearchBox value={search} onChange={setSearch} placeholder="Buscar producto" />
                <Segmented value={view} options={VIEW_OPTIONS} onChange={setView} label="Vista del catálogo" />
                {view === 'table' && <CsvButton filename="tiktok-catalogo.csv" rows={csvRows} />}
                {filtered && (
                  <button
                    type="button"
                    onClick={() => {
                      setFilters(NO_FILTERS)
                      setSearch('')
                    }}
                    className="text-muted-foreground hover:text-foreground rounded border px-2 py-1.5 text-xs hover:bg-slate-100"
                  >
                    Limpiar filtros
                  </button>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {rows.length === 0 ? (
                <EmptyChart>Ningún producto coincide con los filtros.</EmptyChart>
              ) : view === 'gallery' ? (
                <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
                  {rows.map((p) => (
                    <GalleryCard key={p.product_id} p={p} onOpen={open} />
                  ))}
                </ul>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <SortHead sortKey="title" label="Producto" sort={sort} onSort={onSort} right={false} />
                        <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
                        <SortHead sortKey="audit_status" label="Auditoría" sort={sort} onSort={onSort} right={false} />
                        <SortHead sortKey="listing_quality_tier" label="Calidad" sort={sort} onSort={onSort} right={false} />
                        <SortHead sortKey="min_price" label="Precio" sort={sort} onSort={onSort} />
                        <SortHead sortKey="gmv_30d" label="GMV" sort={sort} onSort={onSort} />
                        <SortHead sortKey="settled_sales" label="Ventas liquidadas" sort={sort} onSort={onSort} />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.map((p) => {
                        const status = badgeOf(STATUS_BADGE, p.status)
                        const audit = badgeOf(AUDIT_BADGE, p.audit_status)
                        const quality = badgeOf(QUALITY_BADGE, p.listing_quality_tier)
                        return (
                          <TableRow key={p.product_id} className={p.problem ? 'bg-red-50' : ''}>
                            <TableCell>
                              <button type="button" onClick={() => open(p)} className="flex items-center gap-2 text-left hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                                <ProductImage src={p.thumb_url ?? p.image_url} title={p.title} className="size-10" />
                                <span>{clip(p.title ?? p.product_id, TITLE_MAX)}</span>
                              </button>
                            </TableCell>
                            <TableCell>
                              <Pill label={status.label} className={status.pill} />
                            </TableCell>
                            <TableCell>
                              <Pill label={audit.label} className={audit.pill} />
                            </TableCell>
                            <TableCell>
                              <Pill label={quality.label} className={quality.pill} />
                            </TableCell>
                            <TableCell className="text-right">{priceText(p)}</TableCell>
                            <TableCell className="text-right">{money(p.gmv_30d)}</TableCell>
                            <TableCell className="text-right font-medium">{money(p.settled_sales)}</TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>

          <Hint>
            Un producto cuenta como “con problemas” si fue rechazado en la auditoría o no está activo. Las ventas liquidadas salen de la capa de utilidad vía el SKU maestro; el GMV es el de la última foto de TikTok.
          </Hint>
        </>
      )}

      {selected && <ProductPanel p={selected} days={s?.sales_days ?? days} onClose={() => setSelectedId(null)} />}
    </div>
  )
}
