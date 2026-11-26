import { useMemo, useState } from 'react'

import { ErrorAlert, Hint, Kpi, NumberField, pct1, Segmented } from '@/components/analytics/ui'
import { EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { MAX_LIMIT, type MarketCreator, type MarketSort, tiktokCreatorMarket } from '@/lib/tiktokCreatorMarket'
import { useAsync } from '@/lib/useAsync'

import { CsvButton, PageTitle, SearchBox, SortHead, type SortState } from '../walmart/walmartUi'
import { clip } from './tiktokCatalogFormat'
import { PAGE_SIZE, SEARCH_DEBOUNCE_MS, useDebounced } from './tiktokCreatorFormat'
import { Pager } from './tiktokCreatorUi'
import { NO_VALUE } from './tiktokFormat'
import {
  SORT_OPTIONS,
  categoriesText,
  displayName,
  engagementText,
  exactGmvShare,
  followersText,
  gmvText,
  handleText,
  isNoSamples,
  isWorkingWith,
  partLabel,
} from './tiktokMarketFormat'
import { MarketCreatorPanel } from './tiktokMarketPanel'
import { CreatorAvatar, MarketBadges, MarketCoverageNotice, MarketFootnotes, MarketScoreBar, PartBars } from './tiktokMarketUi'

type ViewMode = 'gallery' | 'table'
type Filters = {
  category: string
  minFollowers: number
  sort: MarketSort
  hideNoSamples: boolean
  onlyWorking: boolean
}
type BooleanFilter = 'hideNoSamples' | 'onlyWorking'

const ALL = ''
const NAME_MAX = 28
const CATEGORY_MAX = 36
const NO_CATEGORY = 'sin categoría'
const VIEW_OPTIONS: { value: ViewMode; label: string }[] = [
  { value: 'gallery', label: 'Galería' },
  { value: 'table', label: 'Tabla' },
]
const TOGGLES: { key: BooleanFilter; label: string }[] = [
  { key: 'hideNoSamples', label: 'Ocultar a los que no debemos enviar muestras' },
  { key: 'onlyWorking', label: 'Solo los que ya trabajamos' },
]
const INITIAL_FILTERS: Filters = { category: ALL, minFollowers: 0, sort: 'score', hideNoSamples: false, onlyWorking: false }
/** Las columnas que el backend sabe ordenar (siempre de mayor a menor). */
const SORT_LABEL: Record<MarketSort, string> = { score: 'Puntaje', followers: 'Seguidores', gmv: 'GMV', newest: 'Más recientes' }
const sortState = (key: MarketSort): SortState<MarketSort> => ({ key, dir: -1 })

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

function SortSelect({ value, onChange }: { value: MarketSort; onChange: (v: MarketSort) => void }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground text-xs">Ordenar por</span>
      <select value={value} onChange={(e) => onChange(e.target.value as MarketSort)} className="h-9 rounded-md border bg-transparent px-2 text-sm">
        {SORT_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function GalleryCard({ c, onOpen }: { c: MarketCreator; onOpen: (c: MarketCreator) => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(c)}
        className={`flex h-full w-full flex-col gap-2 rounded-lg border p-3 text-left hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none ${isNoSamples(c) ? 'border-red-300 bg-red-50/40' : ''}`}
      >
        <span className="flex items-center gap-3">
          <CreatorAvatar c={c} />
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{clip(displayName(c), NAME_MAX)}</span>
            <span className="text-muted-foreground block truncate text-xs">{handleText(c)}</span>
          </span>
        </span>
        <MarketBadges c={c} />
        <dl className="grid grid-cols-3 gap-1 text-xs">
          <div>
            <dt className="text-muted-foreground">Seguidores</dt>
            <dd className="font-medium">{followersText(c)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">GMV</dt>
            <dd className="font-medium">{gmvText(c)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Engagement*</dt>
            <dd className="font-medium">{engagementText(c)}</dd>
          </div>
        </dl>
        <p className="text-muted-foreground line-clamp-1 text-xs">{clip(categoriesText(c), CATEGORY_MAX)}</p>
        <MarketScoreBar score={c.score} className="mt-auto" />
        <PartBars parts={c.score_parts} />
      </button>
    </li>
  )
}

function CreatorTable({ rows, sort, onSort, onOpen }: { rows: MarketCreator[]; sort: SortState<MarketSort>; onSort: (k: MarketSort) => void; onOpen: (c: MarketCreator) => void }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Creador</TableHead>
            <SortHead sortKey="followers" label="Seguidores" sort={sort} onSort={onSort} />
            <TableHead>Categorías</TableHead>
            <SortHead sortKey="gmv" label="GMV" sort={sort} onSort={onSort} />
            <TableHead className="text-right">Engagement*</TableHead>
            <SortHead sortKey="score" label="Puntaje" sort={sort} onSort={onSort} right={false} />
            <TableHead>Desglose</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((c) => (
            <TableRow key={c.creator_open_id} className={isNoSamples(c) ? 'bg-red-50' : ''}>
              <TableCell>
                <button type="button" onClick={() => onOpen(c)} className="flex items-center gap-2 text-left hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                  <CreatorAvatar c={c} className="size-10" />
                  <span className="space-y-1">
                    <span className="block">{clip(displayName(c), NAME_MAX)}</span>
                    <span className="text-muted-foreground block text-xs">{handleText(c)}</span>
                    <MarketBadges c={c} />
                  </span>
                </button>
              </TableCell>
              <TableCell className="text-right">{followersText(c)}</TableCell>
              <TableCell>{clip(categoriesText(c), CATEGORY_MAX)}</TableCell>
              <TableCell className="text-right whitespace-nowrap">{gmvText(c)}</TableCell>
              <TableCell className="text-right">{engagementText(c)}</TableCell>
              <TableCell className="min-w-32">
                <MarketScoreBar score={c.score} />
              </TableCell>
              <TableCell className="min-w-48">
                <PartBars parts={c.score_parts} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

const csvRow = (c: MarketCreator) => {
  const part = (name: string) => c.score_parts.find((p) => p.name === name)?.points ?? null
  return {
    Usuario: c.username,
    Apodo: c.nickname,
    Seguidores: c.followers,
    Tamaño: c.size_tier,
    Categorías: c.categories.join(' | '),
    'GMV (rango o importe)': gmvText(c),
    'Unidades (rango)': c.units_sold_range,
    'Engagement (%) (unidad asumida)': c.engagement_rate == null ? null : Number((c.engagement_rate * 100).toFixed(2)),
    Puntaje: c.score,
    [partLabel('ajuste')]: part('ajuste'),
    [partLabel('rendimiento')]: part('rendimiento'),
    [partLabel('tamaño')]: part('tamaño'),
    Recomendación: c.recommendation,
    'Razón de la relación': c.relation_reason,
  }
}

export function TikTokMarketCreators({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  const scope = useScopeDescription()
  const status = useAsync(() => tiktokCreatorMarket.status(), [])
  const summary = useAsync(() => tiktokCreatorMarket.summary(), [])

  const [view, setView] = useState<ViewMode>('gallery')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<Filters>(INITIAL_FILTERS)
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<MarketCreator | null>(null)
  const debouncedSearch = useDebounced(search, SEARCH_DEBOUNCE_MS)

  // Los filtros "ocultar no enviar muestras" y "solo los que ya trabajamos" no existen en el backend: se aplican en
  // pantalla sobre los MAX_LIMIT primeros del orden elegido. Sin ellos se pagina en el servidor de 20 en 20.
  const clientFiltered = filters.hideNoSamples || filters.onlyWorking
  const offset = clientFiltered ? 0 : page * PAGE_SIZE
  const limit = clientFiltered ? MAX_LIMIT : PAGE_SIZE
  const { category, minFollowers, sort } = filters
  const result = useAsync(
    () => tiktokCreatorMarket.marketplace({ q: debouncedSearch, category, minFollowers, sort, limit, offset }),
    [debouncedSearch, category, minFollowers, sort, limit, offset],
  )
  const data = result.data

  const filtered = useMemo(
    () => (data?.rows ?? []).filter((c) => (!filters.hideNoSamples || !isNoSamples(c)) && (!filters.onlyWorking || isWorkingWith(c))),
    [data?.rows, filters.hideNoSamples, filters.onlyWorking],
  )
  const total = clientFiltered ? filtered.length : (data?.total ?? 0)
  const pages = Math.ceil(total / PAGE_SIZE)
  const rows = clientFiltered ? filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE) : filtered
  const categories = useMemo(
    () => (summary.data?.by_category ?? []).map((c) => c.category).filter((c) => c !== NO_CATEGORY),
    [summary.data?.by_category],
  )
  const s = summary.data

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
      <PageTitle title="Buscar creadores" scope={scope} note="Mercado de creadores afiliados de TikTok Shop (datos públicos). Solo lectura." />

      <ErrorAlert message={status.error ?? summary.error} />
      <MarketCoverageNotice status={status.data} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Creadores cargados" value={s ? count(s.creators) : NO_VALUE} hint={s ? `${count(s.new)} nuevos esta semana` : undefined} />
        <Kpi title="Cobertura con GMV exacto" value={pct1(exactGmvShare(status.data))} hint="el resto trae solo un rango" />
        <Kpi title="Candidatos fuertes" value={s ? count(s.candidates) : NO_VALUE} hint="buen ajuste, rendimiento y tamaño" />
        <Kpi title="A evitar" value={s ? count(s.excluded) : NO_VALUE} hint="sin ventas tras muestras, pausados o que ya trabajamos" className={s && s.excluded > 0 ? 'text-red-600' : ''} />
      </div>

      <Card>
        <CardHeader className="space-y-3">
          <CardTitle>
            Creadores ({count(total)}
            {clientFiltered ? ` entre los ${MAX_LIMIT} primeros` : ''})
          </CardTitle>
          <div className="flex flex-wrap items-end gap-3">
            <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(0) }} placeholder="Buscar por usuario" />
            <CategorySelect value={category} options={categories} onChange={(v) => update({ category: v })} />
            <NumberField label="Seguidores mínimos" value={minFollowers} onCommit={(v) => update({ minFollowers: v })} min={0} step={1000} />
            <SortSelect value={sort} onChange={(v) => update({ sort: v })} />
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            {TOGGLES.map((t) => (
              <ToggleFilter key={t.key} label={t.label} checked={filters[t.key]} onChange={(v) => update({ [t.key]: v })} />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Segmented value={view} options={VIEW_OPTIONS} onChange={setView} label="Vista de creadores" />
            <CsvButton filename="tiktok-mercado-creadores.csv" rows={rows.map(csvRow)} />
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
          {data && rows.length === 0 && <EmptyChart>Ningún creador coincide con los filtros.</EmptyChart>}
          {data && rows.length > 0 && (
            <div aria-busy={result.loading}>
              {view === 'gallery' ? (
                <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {rows.map((c) => (
                    <GalleryCard key={c.creator_open_id} c={c} onOpen={setSelected} />
                  ))}
                </ul>
              ) : (
                <CreatorTable rows={rows} sort={sortState(sort)} onSort={(k) => update({ sort: k })} onOpen={setSelected} />
              )}
              <Pager page={page} pages={pages} onChange={setPage} />
            </div>
          )}
        </CardContent>
      </Card>

      <Hint>
        El puntaje (0 a 100) suma ajuste a nuestras categorías, rendimiento reportado y tamaño; abre un creador para ver la cuenta. Orden actual: {SORT_LABEL[sort]}, de mayor a menor. La categoría y los seguidores mínimos se filtran al leer los datos cargados, no en TikTok.
      </Hint>
      <MarketFootnotes />

      {selected && <MarketCreatorPanel c={selected} onClose={() => setSelected(null)} onOpenTab={onOpenTab} />}
    </div>
  )
}
