import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, pct1, Segmented } from '@/components/analytics/ui'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { ChartCard, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { type Collaboration, type CollabKind, tiktokCreatorMarket } from '@/lib/tiktokCreatorMarket'
import { useAsync } from '@/lib/useAsync'

import { compareValues, CsvButton, nextSort, PageTitle, SearchBox, SortHead, type SortState } from '../walmart/walmartUi'
import { clip, matchesSearch } from './tiktokCatalogFormat'
import { NO_VALUE, dateRange, toMs } from './tiktokFormat'
import { COLLAB_KIND_LABEL, collabCreators, collabStatusLabel } from './tiktokMarketFormat'
import { MarketCoverageNotice, SellerCenterLink } from './tiktokMarketUi'

type Tab = Exclude<CollabKind, 'all'>
type SortKey = 'title' | 'commission' | 'status' | 'start' | 'creators'

const TITLE_MAX = 60
const TEXT_KEYS: readonly SortKey[] = ['title', 'status']
const TAB_OPTIONS: { value: Tab; label: string }[] = [
  { value: 'open', label: COLLAB_KIND_LABEL.open },
  { value: 'target', label: COLLAB_KIND_LABEL.target },
]
const INITIAL_SORT: SortState<SortKey> = { key: 'creators', dir: -1 }
const ONGOING_NOTE = 'La API de TikTok solo admite el estado ONGOING (en curso) para las colaboraciones dirigidas; no se pueden consultar con otro estado.'

const sortValue = (c: Collaboration, key: SortKey): string | number | null => {
  switch (key) {
    case 'title':
      return c.title
    case 'commission':
      return c.commission_rate
    case 'status':
      return collabStatusLabel(c.status)
    case 'start':
      return toMs(c.start)
    case 'creators':
      return collabCreators(c)
  }
}

function creatorsText(c: Collaboration): string {
  const parts = [`${count(collabCreators(c))} creadores`]
  if (c.showcase_creators != null) parts.push(`${count(c.showcase_creators)} con vitrina`)
  if (c.kind === 'target' && c.invited != null && c.creators != null) parts.push(`${count(c.invited)} invitados`)
  return parts.join(' · ')
}

function CollabTable({ rows, sort, onSort, open }: { rows: Collaboration[]; sort: SortState<SortKey>; onSort: (k: SortKey) => void; open: boolean }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <SortHead sortKey="title" label={open ? 'Producto' : 'Colaboración'} sort={sort} onSort={onSort} right={false} />
            {open && <SortHead sortKey="commission" label="Comisión" sort={sort} onSort={onSort} />}
            <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
            <SortHead sortKey="start" label="Fechas" sort={sort} onSort={onSort} right={false} />
            <SortHead sortKey="creators" label="Creadores involucrados" sort={sort} onSort={onSort} />
            {!open && <TableHead>Productos</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((c) => (
            <TableRow key={`${c.kind}-${c.id}`}>
              <TableCell>
                {clip(c.title ?? c.id, TITLE_MAX)}
                <span className="text-muted-foreground block text-xs">ID {c.id}</span>
              </TableCell>
              {open && <TableCell className="text-right">{pct1(c.commission_rate)}</TableCell>}
              <TableCell>{collabStatusLabel(c.status)}</TableCell>
              <TableCell className="whitespace-nowrap">{dateRange(c.start, c.end)}</TableCell>
              <TableCell className="text-right">{creatorsText(c)}</TableCell>
              {!open && <TableCell>{c.products == null ? NO_VALUE : count(c.products)}</TableCell>}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

const csvRow = (c: Collaboration) => ({
  Tipo: COLLAB_KIND_LABEL[c.kind],
  ID: c.id,
  Nombre: c.title,
  Estado: collabStatusLabel(c.status),
  'Comisión (%)': c.commission_rate == null ? null : Number((c.commission_rate * 100).toFixed(2)),
  'Precio mínimo': c.price_min == null ? null : money(c.price_min),
  Inicio: c.start,
  Fin: c.end,
  Creadores: collabCreators(c),
  'Creadores con vitrina': c.showcase_creators,
  Invitados: c.invited,
})

export function TikTokMarketCollaborations() {
  const scope = useScopeDescription()
  const status = useAsync(() => tiktokCreatorMarket.status(), [])
  const result = useAsync(() => tiktokCreatorMarket.collaborations('all'), [])
  const [tab, setTab] = useState<Tab>('open')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>(INITIAL_SORT)

  const all = useMemo(() => result.data?.rows ?? [], [result.data?.rows])
  const ofTab = useMemo(() => all.filter((c) => c.kind === tab), [all, tab])
  const rows = useMemo(
    () => ofTab.filter((c) => matchesSearch(search, c.title, c.id)).sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir)),
    [ofTab, search, sort],
  )
  const byStatus = useMemo<DonutSlice[]>(() => {
    const tally = new Map<string, number>()
    for (const c of ofTab) {
      const label = collabStatusLabel(c.status)
      tally.set(label, (tally.get(label) ?? 0) + 1)
    }
    return [...tally].map(([label, value]) => ({ id: label, label, value }))
  }, [ofTab])
  const options = TAB_OPTIONS.map((o) => ({ ...o, count: all.filter((c) => c.kind === o.value).length }))
  const open = tab === 'open'

  return (
    <div className="space-y-6">
      <PageTitle title="Colaboraciones" scope={scope} note="Colaboraciones abiertas y dirigidas de nuestra tienda con creadores afiliados. Solo lectura." />

      <ErrorAlert message={status.error ?? result.error} />
      <MarketCoverageNotice status={status.data} />
      {result.loading && !result.data && <div className="text-muted-foreground text-sm">Cargando…</div>}

      {result.data && (
        <>
          <Segmented value={tab} options={options} onChange={setTab} label="Tipo de colaboración" />

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi title={`${COLLAB_KIND_LABEL[tab]} activas`} value={count(ofTab.length)} />
            {byStatus.slice(0, 3).map((s) => (
              <Kpi key={s.id} title={s.label} value={count(s.value)} hint="por estado" />
            ))}
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader className="space-y-3">
                <CardTitle>
                  {COLLAB_KIND_LABEL[tab]} ({count(rows.length)})
                </CardTitle>
                <div className="flex flex-wrap items-center gap-2">
                  <SearchBox value={search} onChange={setSearch} placeholder={open ? 'Buscar producto' : 'Buscar colaboración'} />
                  <CsvButton filename={`tiktok-colaboraciones-${tab}.csv`} rows={rows.map(csvRow)} />
                </div>
              </CardHeader>
              <CardContent>
                {rows.length === 0 ? (
                  <EmptyChart>{ofTab.length === 0 ? 'No hay colaboraciones de este tipo en lo cargado.' : 'Ninguna colaboración coincide con la búsqueda.'}</EmptyChart>
                ) : (
                  <CollabTable rows={rows} sort={sort} onSort={(k) => setSort((cur) => nextSort(cur, k, TEXT_KEYS))} open={open} />
                )}
              </CardContent>
            </Card>
            <ChartCard title="Por estado" subtitle={`${COLLAB_KIND_LABEL[tab]} según su estado.`} filename={`tiktok-colaboraciones-${tab}-estado`} csv={byStatus.map((s) => ({ Estado: s.label, Colaboraciones: s.value }))}>
              <Donut slices={byStatus} format={count} centerLabel="colaboraciones" />
            </ChartCard>
          </div>
        </>
      )}

      {!open && <Callout tone="info">{ONGOING_NOTE}</Callout>}
      <Hint>Crear o cambiar colaboraciones se hace en el Seller Center de TikTok Shop; esta app solo las lee.</Hint>
      <div>
        <SellerCenterLink />
      </div>
    </div>
  )
}
