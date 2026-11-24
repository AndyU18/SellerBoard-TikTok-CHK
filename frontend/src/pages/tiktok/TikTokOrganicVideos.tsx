import { useEffect, useMemo, useState } from 'react'

import { Segmented } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { tiktokOrganicData, type OrganicSort, type OrganicVideoListItem } from '@/lib/tiktokOrganicData'
import { useAsync } from '@/lib/useAsync'

import { CsvButton, SearchBox, SortHead } from '../walmart/walmartUi'
import { OrganicVideoPanel } from './OrganicVideoPanel'
import { clock, longDate, pct, shortDate, signed, useTrackingSince, videoTitle } from './tiktokOrganicHelpers'
import { OrganicPage, TrackingNote, VideoCover, type OrganicTabProps } from './tiktokOrganicUi'

type ViewMode = 'gallery' | 'table'

const PAGE_SIZE = 24
const SEARCH_DEBOUNCE_MS = 350
const SORT_OPTIONS: { value: OrganicSort; label: string }[] = [
  { value: 'recent', label: 'Más recientes' },
  { value: 'views', label: 'Más vistas' },
  { value: 'likes', label: 'Más likes' },
  { value: 'comments', label: 'Más comentarios' },
  { value: 'shares', label: 'Más compartidos' },
  { value: 'engagement', label: 'Mayor engagement' },
  { value: 'views_per_day', label: 'Más vistas por día' },
]
const VIEW_OPTIONS: { value: ViewMode; label: string }[] = [
  { value: 'gallery', label: 'Galería' },
  { value: 'table', label: 'Tabla' },
]

/** Devuelve `value` tras `delay` ms sin cambios (evita una consulta por tecla). */
function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])
  return debounced
}

const growthLabel = (v: OrganicVideoListItem) => (v.recent_growth ? `${signed(v.recent_growth.delta)} vistas (${v.recent_growth.days} d)` : 'sin historial aún')

function VideoCard({ video, onOpen }: { video: OrganicVideoListItem; onOpen: () => void }) {
  const title = videoTitle(video)
  return (
    <li>
      <button type="button" onClick={onOpen} className="group w-full space-y-2 rounded-lg border p-2 text-left transition hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
        <div className="relative">
          <VideoCover url={video.cover_url} alt={title} className="aspect-[9/16] w-full rounded-md" />
          <span className="absolute right-1 bottom-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-white">{clock(video.duration_seconds)}</span>
        </div>
        <div className="line-clamp-2 min-h-8 text-xs font-medium">{title}</div>
        <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px]">
          <dt className="text-muted-foreground">Vistas</dt>
          <dd className="text-right font-medium tabular-nums">{count(video.views)}</dd>
          <dt className="text-muted-foreground">Likes</dt>
          <dd className="text-right tabular-nums">{count(video.likes)}</dd>
          <dt className="text-muted-foreground">Coment.</dt>
          <dd className="text-right tabular-nums">{count(video.comments)}</dd>
          <dt className="text-muted-foreground">Compart.</dt>
          <dd className="text-right tabular-nums">{count(video.shares)}</dd>
          <dt className="text-muted-foreground">Engagement</dt>
          <dd className="text-right tabular-nums">{pct(video.engagement)}</dd>
          <dt className="text-muted-foreground">Vistas/día</dt>
          <dd className="text-right tabular-nums">{count(Math.round(video.views_per_day))}</dd>
        </dl>
        <div className="text-muted-foreground text-[11px]">{growthLabel(video)}</div>
      </button>
    </li>
  )
}

function VideosTable({ items, sort, onSort, onOpen }: { items: OrganicVideoListItem[]; sort: OrganicSort; onSort: (s: OrganicSort) => void; onOpen: (v: OrganicVideoListItem) => void }) {
  const state = { key: sort, dir: -1 as const }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortHead sortKey="recent" label="Video / fecha" sort={state} onSort={onSort} right={false} />
          <SortHead sortKey="views" label="Vistas" sort={state} onSort={onSort} />
          <SortHead sortKey="likes" label="Likes" sort={state} onSort={onSort} />
          <SortHead sortKey="comments" label="Comentarios" sort={state} onSort={onSort} />
          <SortHead sortKey="shares" label="Compartidos" sort={state} onSort={onSort} />
          <SortHead sortKey="engagement" label="Engagement" sort={state} onSort={onSort} />
          <SortHead sortKey="views_per_day" label="Vistas/día" sort={state} onSort={onSort} />
          <TableHead className="text-right">Crecimiento 7 d</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((v) => (
          <TableRow key={v.video_id}>
            <TableCell className="max-w-xs">
              <button type="button" onClick={() => onOpen(v)} className="flex items-center gap-2 text-left hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                <VideoCover url={v.cover_url} alt={videoTitle(v)} className="h-12 w-9 shrink-0 rounded" />
                <span className="min-w-0">
                  <span className="line-clamp-2 text-sm">{videoTitle(v)}</span>
                  <span className="text-muted-foreground block text-xs">{shortDate(v.created_at ?? '—')} · {clock(v.duration_seconds)}</span>
                </span>
              </button>
            </TableCell>
            <TableCell className="text-right font-medium">{count(v.views)}</TableCell>
            <TableCell className="text-right">{count(v.likes)}</TableCell>
            <TableCell className="text-right">{count(v.comments)}</TableCell>
            <TableCell className="text-right">{count(v.shares)}</TableCell>
            <TableCell className="text-right">{pct(v.engagement)}</TableCell>
            <TableCell className="text-right">{count(Math.round(v.views_per_day))}</TableCell>
            <TableCell className="text-right">{v.recent_growth ? signed(v.recent_growth.delta) : '—'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Galería (o tabla) de los videos de la cuenta con orden, búsqueda, paginación y detalle. */
export function TikTokOrganicVideos({ onOpenTab }: OrganicTabProps) {
  const [sort, setSort] = useState<OrganicSort>('recent')
  const [mode, setMode] = useState<ViewMode>('gallery')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<OrganicVideoListItem | null>(null)
  const q = useDebounced(search.trim(), SEARCH_DEBOUNCE_MS)
  const since = useTrackingSince()
  const state = useAsync(() => tiktokOrganicData.videos({ sort, q, limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [sort, q, page])

  const items = state.data?.items
  const csvRows = useMemo(
    () =>
      (items ?? []).map((v) => ({
        Video: videoTitle(v),
        Publicado: longDate(v.created_at),
        'Duración (s)': v.duration_seconds,
        Vistas: v.views,
        Likes: v.likes,
        Comentarios: v.comments,
        Compartidos: v.shares,
        Engagement: pct(v.engagement),
        'Vistas por día': Math.round(v.views_per_day),
        'Vistas ganadas 7 d': v.recent_growth?.delta ?? '',
        Enlace: v.share_url ?? '',
      })),
    [items],
  )
  const changeSort = (value: OrganicSort) => {
    setSort(value)
    setPage(0)
  }

  return (
    <OrganicPage title="TikTok orgánico · Videos" state={state} isEmpty={(d) => d.total === 0 && !q} onOpenTab={onOpenTab}>
      {(d) => {
        const lastPage = Math.max(Math.ceil(d.total / PAGE_SIZE) - 1, 0)
        return (
          <>
            <TrackingNote since={since} />
            <Card>
              <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <CardTitle>{count(d.total)} videos</CardTitle>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2 text-sm">
                    <span className="text-muted-foreground">Ordenar</span>
                    <select value={sort} onChange={(e) => changeSort(e.target.value as OrganicSort)} className="h-9 rounded-md border bg-transparent px-2 text-sm">
                      {SORT_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <SearchBox
                    value={search}
                    onChange={(v) => {
                      setSearch(v)
                      setPage(0)
                    }}
                    placeholder="Buscar en el texto del video"
                  />
                  <Segmented value={mode} options={VIEW_OPTIONS} onChange={setMode} label="Vista" />
                  {mode === 'table' && <CsvButton filename="tiktok-organico-videos.csv" rows={csvRows} />}
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {d.items.length === 0 ? (
                  <p className="text-muted-foreground py-8 text-center text-sm">Ningún video coincide con la búsqueda.</p>
                ) : mode === 'gallery' ? (
                  <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
                    {d.items.map((v) => (
                      <VideoCard key={v.video_id} video={v} onOpen={() => setSelected(v)} />
                    ))}
                  </ul>
                ) : (
                  <div className="overflow-x-auto">
                    <VideosTable items={d.items} sort={sort} onSort={changeSort} onOpen={setSelected} />
                  </div>
                )}
                <div className="flex items-center justify-between gap-2 text-sm">
                  <Button type="button" variant="outline" size="sm" disabled={page === 0 || state.loading} onClick={() => setPage((p) => Math.max(p - 1, 0))}>
                    Anterior
                  </Button>
                  <span className="text-muted-foreground">
                    Página {page + 1} de {lastPage + 1}
                  </span>
                  <Button type="button" variant="outline" size="sm" disabled={page >= lastPage || state.loading} onClick={() => setPage((p) => p + 1)}>
                    Siguiente
                  </Button>
                </div>
              </CardContent>
            </Card>
            {selected && <OrganicVideoPanel video={selected} onClose={() => setSelected(null)} />}
          </>
        )
      }}
    </OrganicPage>
  )
}
