import { useEffect, useMemo, useState } from 'react'

import { ErrorAlert, Hint, Kpi, NumberField, pct1, Segmented } from '@/components/analytics/ui'
import { EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow, TableHead } from '@/components/ui/table'
import { count } from '@/lib/format'
import { type CreatorOpportunity, MAX_LIMIT, type OpportunitySort, tiktokCreator } from '@/lib/tiktokCreatorCatalog'
import { useAsync } from '@/lib/useAsync'

import { CsvButton, PageTitle, SearchBox, SortHead, type SortState } from '../walmart/walmartUi'
import { clip } from './tiktokCatalogFormat'
import { ProductImage } from './tiktokCatalogUi'
import {
  clearPendingCategory,
  NO_CATEGORY,
  PAGE_SIZE,
  peekPendingCategory,
  perUnitText,
  priceText,
  rateText,
  relatesToOurCatalog,
  SEARCH_DEBOUNCE_MS,
  SORT_OPTIONS,
  unitsText,
  useDebounced,
} from './tiktokCreatorFormat'
import { CreatorProductPanel } from './tiktokCreatorPanel'
import { CoverageNotice, OpportunityBadges, Pager, ScoreBar } from './tiktokCreatorUi'
import { NO_VALUE } from './tiktokFormat'

type ViewMode = 'gallery' | 'table'
type Filters = {
  category: string
  minCommission: number
  minUnits: number
  sort: OpportunitySort
  onlyNew: boolean
  onlyRelated: boolean
  includeSuspect: boolean
}
type BooleanFilter = 'onlyNew' | 'onlyRelated' | 'includeSuspect'

const ALL = ''
const TITLE_MAX = 60
const VIEW_OPTIONS: { value: ViewMode; label: string }[] = [
  { value: 'gallery', label: 'Galería' },
  { value: 'table', label: 'Tabla' },
]
const TOGGLES: { key: BooleanFilter; label: string }[] = [
  { key: 'onlyNew', label: 'Solo nuevos esta semana' },
  { key: 'onlyRelated', label: 'Solo los que se relacionan con nuestro catálogo' },
  { key: 'includeSuspect', label: 'Mostrar datos sospechosos' },
]
const INITIAL_FILTERS: Filters = { category: ALL, minCommission: 0, minUnits: 0, sort: 'score', onlyNew: false, onlyRelated: false, includeSuspect: false }
/** Las columnas de la tabla que el backend sabe ordenar (siempre de mayor a menor). */
const SORT_LABEL: Record<OpportunitySort, string> = { score: 'Puntaje', commission: 'Comisión por unidad', units: 'Unidades', price: 'Precio', newest: 'Más recientes' }
const sortState = (key: OpportunitySort): SortState<OpportunitySort> => ({ key, dir: -1 })

function ToggleFilter({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="size-4" />
      {label}
    </label>
  )
}

function CategorySelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const list = value && !options.includes(value) ? [value, ...options] : options
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground text-xs">Categoría</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="h-9 max-w-56 rounded-md border bg-transparent px-2 text-sm">
        <option value={ALL}>Todas</option>
        {list.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </label>
  )
}

function SortSelect({ value, onChange }: { value: OpportunitySort; onChange: (v: OpportunitySort) => void }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground text-xs">Ordenar por</span>
      <select value={value} onChange={(e) => onChange(e.target.value as OpportunitySort)} className="h-9 rounded-md border bg-transparent px-2 text-sm">
        {SORT_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function GalleryCard({ p, onOpen }: { p: CreatorOpportunity; onOpen: (p: CreatorOpportunity) => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(p)}
        className={`flex h-full w-full flex-col gap-2 rounded-lg border p-2 text-left hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none ${p.suspect ? 'border-red-300 bg-red-50/40' : ''}`}
      >
        <ProductImage src={p.image_url} title={p.title} className="aspect-square w-full" />
        <span className="line-clamp-2 text-sm font-medium">{p.title ?? p.product_id}</span>
        <OpportunityBadges p={p} />
        <dl className="grid grid-cols-3 gap-1 text-xs">
          <div>
            <dt className="text-muted-foreground">Comisión</dt>
            <dd className="font-medium">{rateText(p)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Precio</dt>
            <dd className="font-medium">{priceText(p)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Unidades</dt>
            <dd className="font-medium">{unitsText(p)}</dd>
          </div>
        </dl>
        <ScoreBar score={p.score} className="mt-auto" />
      </button>
    </li>
  )
}

function OpportunityTable({ rows, sort, onSort, onOpen }: { rows: CreatorOpportunity[]; sort: SortState<OpportunitySort>; onSort: (k: OpportunitySort) => void; onOpen: (p: CreatorOpportunity) => void }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Producto</TableHead>
            <TableHead>Categoría</TableHead>
            <SortHead sortKey="commission" label="Comisión" sort={sort} onSort={onSort} />
            <SortHead sortKey="price" label="Precio" sort={sort} onSort={onSort} />
            <SortHead sortKey="units" label="Unidades" sort={sort} onSort={onSort} />
            <SortHead sortKey="score" label="Puntaje" sort={sort} onSort={onSort} />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((p) => (
            <TableRow key={p.product_id} className={p.suspect ? 'bg-red-50' : ''}>
              <TableCell>
                <button type="button" onClick={() => onOpen(p)} className="flex items-center gap-2 text-left hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                  <ProductImage src={p.image_url} title={p.title} className="size-10" />
                  <span className="space-y-1">
                    <span className="block">{clip(p.title ?? p.product_id, TITLE_MAX)}</span>
                    <OpportunityBadges p={p} />
                  </span>
                </button>
              </TableCell>
              <TableCell>{clip(p.category_leaf ?? p.category, TITLE_MAX)}</TableCell>
              <TableCell className="text-right">
                {rateText(p)}
                <span className="text-muted-foreground block text-xs">{perUnitText(p)} / unidad</span>
              </TableCell>
              <TableCell className="text-right">{priceText(p)}</TableCell>
              <TableCell className="text-right">{unitsText(p)}</TableCell>
              <TableCell className="min-w-32">
                <ScoreBar score={p.score} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

const csvRow = (p: CreatorOpportunity) => ({
  Producto: p.title,
  ID: p.product_id,
  Categoría: p.category,
  'Comisión (%)': p.suspect || p.commission_rate == null ? null : Number((p.commission_rate * 100).toFixed(2)),
  'Comisión por unidad': p.suspect ? null : p.commission_per_unit,
  Precio: p.suspect ? null : p.price,
  Unidades: p.suspect ? null : p.units_sold,
  Puntaje: p.score,
  Nuevo: p.is_new ? 'Sí' : 'No',
  'Ya vendemos algo así': p.relation.equivalent ? 'Sí' : 'No',
  'Misma categoría': p.relation.sells_same_category ? 'Sí' : 'No',
  Tienda: p.shop_name,
  Enlace: p.detail_link,
})

export function TikTokCreatorOpportunities() {
  const scope = useScopeDescription()
  const status = useAsync(() => tiktokCreator.status(), [])
  const summary = useAsync(() => tiktokCreator.summary(), [])

  const [view, setView] = useState<ViewMode>('gallery')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<Filters>(() => ({ ...INITIAL_FILTERS, category: peekPendingCategory() }))
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<CreatorOpportunity | null>(null)
  const debouncedSearch = useDebounced(search, SEARCH_DEBOUNCE_MS)

  // La categoría que dejó Panorama se usa una sola vez: se borra al montar para no reaparecer en la próxima visita.
  useEffect(() => clearPendingCategory(), [])

  // Los filtros "solo nuevos" y "solo relacionados" no existen en el backend: se aplican en pantalla sobre
  // los MAX_LIMIT primeros del orden elegido. Sin ellos se pagina en el servidor de 20 en 20.
  const clientFiltered = filters.onlyNew || filters.onlyRelated
  const offset = clientFiltered ? 0 : page * PAGE_SIZE
  const limit = clientFiltered ? MAX_LIMIT : PAGE_SIZE
  const { category, minCommission, minUnits, sort, includeSuspect } = filters
  const result = useAsync(
    () => tiktokCreator.opportunities({ q: debouncedSearch, category, minCommission, minUnits, sort, limit, offset, includeSuspect }),
    [debouncedSearch, category, minCommission, minUnits, sort, limit, offset, includeSuspect],
  )
  const data = result.data

  const filtered = useMemo(
    () => (data?.items ?? []).filter((p) => (!filters.onlyNew || p.is_new) && (!filters.onlyRelated || relatesToOurCatalog(p))),
    [data?.items, filters.onlyNew, filters.onlyRelated],
  )
  const total = clientFiltered ? filtered.length : (data?.total ?? 0)
  const pages = Math.ceil(total / PAGE_SIZE)
  const rows = clientFiltered ? filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE) : filtered
  const categories = useMemo(
    () => (summary.data?.by_category ?? []).map((c) => c.category).filter((c) => c !== NO_CATEGORY),
    [summary.data?.by_category],
  )
  const affineProducts = (summary.data?.by_category ?? []).reduce((sum, c) => sum + c.affine, 0)
  const coverage = status.data?.coverage

  const update = (patch: Partial<Filters>) => {
    setFilters((cur) => ({ ...cur, ...patch }))
    setPage(0)
  }
  const reset = () => {
    setFilters(INITIAL_FILTERS)
    setSearch('')
    setPage(0)
  }
  const dirty = search !== '' || JSON.stringify(filters) !== JSON.stringify(INITIAL_FILTERS)

  return (
    <div className="space-y-6">
      <PageTitle title="Oportunidades de productos para promocionar" scope={scope} note="Catálogo de afiliados de nuestra cuenta de creador. Solo lectura." />

      <ErrorAlert message={status.error ?? summary.error} />
      <CoverageNotice status={status.data} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Productos cargados" value={coverage ? count(coverage.loaded) : NO_VALUE} hint={coverage?.total != null ? `de ${count(coverage.total)} en el catálogo` : 'total no informado por TikTok'} />
        <Kpi title="Comisión promedio" value={pct1(summary.data?.avg_commission_rate)} hint="sin datos sospechosos" />
        <Kpi title="Nuevos esta semana" value={count(summary.data?.new_this_week.count)} />
        <Kpi title="Afines a nuestro negocio" value={summary.data ? count(affineProducts) : NO_VALUE} hint="hogar, cocina, navidad o empaque" />
      </div>

      <Card>
        <CardHeader className="space-y-3">
          <CardTitle>
            Productos ({count(total)}
            {clientFiltered ? ` entre los ${MAX_LIMIT} primeros` : ''})
          </CardTitle>
          <div className="flex flex-wrap items-end gap-3">
            <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(0) }} placeholder="Buscar producto" />
            <CategorySelect value={category} options={categories} onChange={(v) => update({ category: v })} />
            <NumberField label="Comisión mínima" value={minCommission} onCommit={(v) => update({ minCommission: v })} min={0} max={100} step={0.5} suffix="%" />
            <NumberField label="Unidades mínimas" value={minUnits} onCommit={(v) => update({ minUnits: v })} min={0} />
            <SortSelect value={sort} onChange={(v) => update({ sort: v })} />
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            {TOGGLES.map((t) => (
              <ToggleFilter key={t.key} label={t.label} checked={filters[t.key]} onChange={(v) => update({ [t.key]: v })} />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Segmented value={view} options={VIEW_OPTIONS} onChange={setView} label="Vista de oportunidades" />
            {view === 'table' && <CsvButton filename="tiktok-creator-oportunidades.csv" rows={rows.map(csvRow)} />}
            {dirty && (
              <button type="button" onClick={reset} className="text-muted-foreground hover:text-foreground rounded border px-2 py-1.5 text-xs hover:bg-slate-100">
                Limpiar filtros
              </button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <ErrorAlert message={result.error} />
          {result.loading && !data && <div className="text-muted-foreground text-sm">Cargando…</div>}
          {data && rows.length === 0 && <EmptyChart>Ningún producto coincide con los filtros.</EmptyChart>}
          {data && rows.length > 0 && (
            <div aria-busy={result.loading}>
              {view === 'gallery' ? (
                <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                  {rows.map((p) => (
                    <GalleryCard key={p.product_id} p={p} onOpen={setSelected} />
                  ))}
                </ul>
              ) : (
                <OpportunityTable rows={rows} sort={sortState(sort)} onSort={(k) => update({ sort: k })} onOpen={setSelected} />
              )}
              <Pager page={page} pages={pages} onChange={setPage} />
            </div>
          )}
        </CardContent>
      </Card>

      <Hint>
        El puntaje (0 a 100) combina comisión por unidad, demanda, disponibilidad y parecido con nuestro negocio; abre un producto para ver la cuenta. Orden actual: {SORT_LABEL[sort]}, de mayor a menor. Los productos con datos de prueba absurdos se ocultan por defecto y, si los muestras, aparecen sin importes.
      </Hint>

      {selected && data && <CreatorProductPanel p={selected} formula={data.score_formula} constants={data.constants} onClose={() => setSelected(null)} />}
    </div>
  )
}
