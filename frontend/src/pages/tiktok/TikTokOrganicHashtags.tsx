import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, Segmented } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisNumber, axisPct, ChartCard, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokOrganicData, type TagRow } from '@/lib/tiktokOrganicData'
import { useAsync } from '@/lib/useAsync'

import { compareValues, nextSort, type SortState } from '../walmart/walmartSort'
import { CsvButton, SearchBox, SortHead } from '../walmart/walmartUi'
import { avgViews, pct, useTrackingSince } from './tiktokOrganicHelpers'
import { OrganicPage, TrackingNote, type OrganicTabProps } from './tiktokOrganicUi'

type TagKind = 'hashtags' | 'mentions'
type SortKey = 'tag' | 'videos' | 'avg_views' | 'total_views' | 'engagement'

const TEXT_KEYS: readonly SortKey[] = ['tag']
const TOP_BARS = 15
const KIND_OPTIONS: { value: TagKind; label: string }[] = [
  { value: 'hashtags', label: 'Hashtags' },
  { value: 'mentions', label: 'Menciones' },
]
const DURATION_SERIES: ComboSeries[] = [
  { key: 'avg_views', label: 'Vistas medias', type: 'bar', color: '#3b82f6', format: avgViews },
  { key: 'engagement', label: 'Engagement', type: 'line', axis: 'right', color: '#ec4899', format: pct },
]

const prefix = (kind: TagKind) => (kind === 'hashtags' ? '#' : '@')

/** Tabla ordenable con búsqueda de hashtags o menciones. */
function TagTable({ kind, rows }: { kind: TagKind; rows: TagRow[] }) {
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'videos', dir: -1 })
  const [search, setSearch] = useState('')
  const sorted = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return rows.filter((r) => !needle || r.tag.includes(needle)).sort((a, b) => compareValues(a[sort.key], b[sort.key], sort.dir))
  }, [rows, search, sort])
  const csv = sorted.map((r) => ({ [kind === 'hashtags' ? 'Hashtag' : 'Mención']: `${prefix(kind)}${r.tag}`, Videos: r.videos, 'Vistas medias': r.avg_views, 'Vistas totales': r.total_views, Engagement: pct(r.engagement) }))
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))
  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <CardTitle>Detalle</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={search} onChange={setSearch} placeholder={kind === 'hashtags' ? 'Buscar hashtag' : 'Buscar mención'} />
          <CsvButton filename={`tiktok-organico-${kind}.csv`} rows={csv} />
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead sortKey="tag" label={kind === 'hashtags' ? 'Hashtag' : 'Mención'} sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="videos" label="Videos" sort={sort} onSort={onSort} />
              <SortHead sortKey="avg_views" label="Vistas medias" sort={sort} onSort={onSort} />
              <SortHead sortKey="total_views" label="Vistas totales" sort={sort} onSort={onSort} />
              <SortHead sortKey="engagement" label="Engagement" sort={sort} onSort={onSort} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                  Nada coincide.
                </TableCell>
              </TableRow>
            )}
            {sorted.map((r) => (
              <TableRow key={r.tag}>
                <TableCell className="font-medium">
                  {prefix(kind)}
                  {r.tag}
                </TableCell>
                <TableCell className="text-right">{count(r.videos)}</TableCell>
                <TableCell className="text-right">{avgViews(r.avg_views)}</TableCell>
                <TableCell className="text-right">{count(r.total_views)}</TableCell>
                <TableCell className="text-right">{pct(r.engagement)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

/** Hashtags y menciones con su rendimiento, y la duración de video que mejor funciona. */
export function TikTokOrganicHashtags({ onOpenTab }: OrganicTabProps) {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const state = useAsync(() => tiktokOrganicData.hashtags(days), [days])
  const since = useTrackingSince()
  const [kind, setKind] = useState<TagKind>('hashtags')

  return (
    <OrganicPage
      title="TikTok orgánico · Hashtags y duración"
      note={state.data ? `${count(state.data.videos)} videos publicados en los últimos ${state.data.days} días` : undefined}
      state={state}
      isEmpty={(d) => d.videos === 0}
      scopedToPeriod
      onOpenTab={onOpenTab}
    >
      {(d) => {
        const rows = d[kind]
        const bars: HBarRow[] = [...rows]
          .sort((a, b) => (b.avg_views ?? 0) - (a.avg_views ?? 0))
          .slice(0, TOP_BARS)
          .map((r) => ({ id: r.tag, label: `${prefix(kind)}${r.tag}`, avg_views: r.avg_views ?? 0 }))
        return (
          <>
            <TrackingNote since={since} />
            {d.videos_without_hashtags > 0 && (
              <Callout tone="warning">
                {count(d.videos_without_hashtags)} de {count(d.videos)} videos no tienen hashtags en el texto. Los hashtags ayudan a que TikTok clasifique el video: prueba a incluir algunos.
              </Callout>
            )}
            <Segmented value={kind} options={KIND_OPTIONS.map((o) => ({ ...o, count: d[o.value].length }))} onChange={setKind} label="Tipo de etiqueta" />
            <ChartCard title={`${kind === 'hashtags' ? 'Hashtags' : 'Menciones'} con más vistas medias`} subtitle={`Los ${TOP_BARS} primeros. Compara con cuántos videos lo usan en la tabla.`} filename={`tiktok-organico-${kind}-vistas`}>
              {bars.length ? <HBarChart rows={bars} series={[{ key: 'avg_views', label: 'Vistas medias' }]} format={axisNumber} labelWidth={120} /> : <EmptyChart>No hay {kind === 'hashtags' ? 'hashtags' : 'menciones'} en los videos del período.</EmptyChart>}
            </ChartCard>
            {rows.length > 0 && <TagTable key={kind} kind={kind} rows={rows} />}
            <ChartCard
              title="Duración óptima del video"
              subtitle="Vistas medias y engagement por rango de duración (solo videos con duración conocida)"
              filename="tiktok-organico-duracion"
              csv={d.durations.map((r) => ({ Duración: r.label, Videos: r.videos, 'Vistas medias': r.avg_views, Engagement: pct(r.engagement) }))}
            >
              {d.best_duration && (
                <p className="mb-2 text-sm">
                  Los videos de <strong>{d.best_duration}</strong> son los que más vistas medias logran.
                </p>
              )}
              <ComboChart data={d.durations.map((r) => ({ ...r }))} xKey="label" series={DURATION_SERIES} height={260} leftFormat={axisNumber} rightFormat={axisPct} />
            </ChartCard>
          </>
        )
      }}
    </OrganicPage>
  )
}
