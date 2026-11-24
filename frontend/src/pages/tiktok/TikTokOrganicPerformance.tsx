import { useMemo, useState } from 'react'

import { Callout, Kpi } from '@/components/analytics/ui'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisNumber, axisPct, ChartCard, EmptyChart } from '@/components/charts/kit'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokOrganicData, VIDEOS_MAX_LIMIT, type OrganicPerformance, type OrganicVideo, type OrganicVideosResponse, type ViralVideo } from '@/lib/tiktokOrganicData'
import { useAsync } from '@/lib/useAsync'

import { OrganicVideoPanel } from './OrganicVideoPanel'
import { avgViews, clock, pct, shortDate, videoTitle } from './tiktokOrganicHelpers'
import { OrganicPage, TrackingNote, VideoRankList, type OrganicTabProps } from './tiktokOrganicUi'

interface PerformanceData {
  performance: OrganicPerformance
  videos: OrganicVideosResponse
}

const GROUP_VIRAL = 'Viral'
const GROUP_NORMAL = 'Normal'

const FOLLOWERS_SERIES: ComboSeries[] = [{ key: 'followers', label: 'Seguidores', type: 'line', color: '#ec4899', format: count }]
const VIEWS_SERIES: ComboSeries[] = [
  { key: 'views_gained', label: 'Vistas ganadas por día', type: 'bar', color: '#3b82f6', format: count },
  { key: 'views_total', label: 'Vistas acumuladas', type: 'line', axis: 'right', color: '#10b981', format: count },
]
const DISTRIBUTION_SERIES: ComboSeries[] = [{ key: 'videos', label: 'Videos', type: 'bar', color: '#8b5cf6', format: count }]

/** Puntos vistas vs engagement; el tamaño es la duración (solo si algún video la trae). */
function bubblePoints(videos: OrganicVideo[], viralIds: Set<string>): BubblePoint[] {
  const hasDuration = videos.some((v) => v.duration_seconds != null)
  return videos
    .filter((v) => v.engagement != null)
    .map((v) => ({
      id: v.video_id,
      label: videoTitle(v),
      x: v.views,
      y: v.engagement ?? 0,
      z: hasDuration ? (v.duration_seconds ?? 0) : undefined,
      group: viralIds.has(v.video_id) ? GROUP_VIRAL : GROUP_NORMAL,
    }))
}

function ViralList({ viral, onSelect }: { viral: OrganicPerformance['viral']; onSelect: (v: OrganicVideo) => void }) {
  if (viral.note) return <Callout tone="info">{viral.note}</Callout>
  if (!viral.videos.length) return <EmptyChart>Ningún video superó {viral.multiple}× la mediana en este período.</EmptyChart>
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-xs">
        Un video “explota” si llega a {viral.multiple}× la mediana del período ({avgViews(viral.median_views)} vistas): desde {avgViews(viral.threshold)} vistas.
      </p>
      <ul className="divide-y">
        {viral.videos.map((v: ViralVideo) => (
          <li key={v.video_id}>
            <button type="button" onClick={() => onSelect(v)} className="flex w-full items-center justify-between gap-3 rounded p-2 text-left hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none dark:hover:bg-slate-900">
              <span className="min-w-0">
                <span className="line-clamp-1 text-sm font-medium">{videoTitle(v)}</span>
                <span className="text-muted-foreground block text-xs">
                  {count(v.views)} vistas = {v.times_median.toFixed(1)}× la mediana · engagement {pct(v.engagement)} · {clock(v.duration_seconds)}
                </span>
              </span>
              <span className="shrink-0 rounded bg-pink-100 px-2 py-0.5 text-xs font-medium text-pink-800">{v.times_median.toFixed(1)}×</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Rendimiento de la cuenta: series, top y virales, distribución de vistas y vistas contra engagement. */
export function TikTokOrganicPerformance({ onOpenTab }: OrganicTabProps) {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const state = useAsync<PerformanceData>(async () => {
    const [performance, videos] = await Promise.all([
      tiktokOrganicData.performance(days),
      tiktokOrganicData.videos({ sort: 'views', limit: VIDEOS_MAX_LIMIT, offset: 0 }),
    ])
    return { performance, videos }
  }, [days])
  const [selected, setSelected] = useState<OrganicVideo | null>(null)
  const data = state.data
  const points = useMemo(
    () => (data ? bubblePoints(data.videos.items, new Set(data.performance.viral.videos.map((v) => v.video_id))) : []),
    [data],
  )

  return (
    <OrganicPage
      title="TikTok orgánico · Rendimiento"
      note={`Videos publicados en los últimos ${days} días`}
      state={state}
      isEmpty={(d) => !d.performance.tracking_since && d.videos.total === 0}
      onOpenTab={onOpenTab}
    >
      {({ performance, videos }) => (
        <>
          <TrackingNote since={performance.tracking_since} />
          <div className="grid gap-4 sm:grid-cols-3">
            <Kpi title="Videos con rendimiento alto" value={count(performance.viral.videos.length)} hint={`≥ ${performance.viral.multiple}× la mediana`} />
            <Kpi title="Mediana de vistas" value={avgViews(performance.viral.median_views)} hint="del período elegido" />
            <Kpi title="Promedio de vistas" value={avgViews(performance.viral.mean_views)} hint="si es mucho mayor que la mediana, pocos videos arrastran la cuenta" />
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="Seguidores" subtitle="Una foto por día desde la conexión" filename="tiktok-organico-rendimiento-seguidores" csv={performance.followers_series.map((p) => ({ Fecha: p.date, Seguidores: p.followers }))}>
              <ComboChart data={performance.followers_series.map((p) => ({ ...p }))} xKey="date" series={FOLLOWERS_SERIES} height={260} xFormat={shortDate} leftFormat={axisNumber} />
            </ChartCard>
            <ChartCard title="Vistas" subtitle="Vistas ganadas entre fotos y total acumulado" filename="tiktok-organico-rendimiento-vistas" csv={performance.views_series.map((p) => ({ Fecha: p.date, 'Vistas totales': p.views_total, 'Vistas ganadas': p.views_gained }))}>
              <ComboChart data={performance.views_series.map((p) => ({ ...p }))} xKey="date" series={VIEWS_SERIES} height={260} xFormat={shortDate} leftFormat={axisNumber} rightFormat={axisNumber} />
            </ChartCard>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="Mejores videos" subtitle="Por vistas. Un clic abre el detalle." filename="tiktok-organico-top-videos">
              {performance.top_videos.length ? <VideoRankList videos={performance.top_videos} onSelect={setSelected} /> : <EmptyChart>Sin videos en el período.</EmptyChart>}
            </ChartCard>
            <ChartCard title="Videos que explotan" subtitle="Comparados con la mediana de vistas del período" filename="tiktok-organico-virales">
              <ViralList viral={performance.viral} onSelect={setSelected} />
            </ChartCard>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Distribución de vistas"
              subtitle="Cuántos videos caen en cada rango de vistas"
              filename="tiktok-organico-distribucion"
              csv={performance.distribution.map((b) => ({ Rango: b.label, Videos: b.videos, Porcentaje: pct(b.share) }))}
            >
              <ComboChart data={performance.distribution.map((b) => ({ ...b }))} xKey="label" series={DISTRIBUTION_SERIES} height={260} leftFormat={axisNumber} />
            </ChartCard>
            <ChartCard
              title="Vistas vs engagement"
              subtitle={`Los ${count(videos.items.length)} videos con más vistas. Tamaño = duración; la leyenda separa los virales.`}
              filename="tiktok-organico-vistas-engagement"
              csv={points.map((p) => ({ Video: p.label, Vistas: p.x, Engagement: pct(p.y), 'Duración (s)': p.z, Grupo: p.group }))}
            >
              <BubbleChart points={points} xLabel="Vistas" yLabel="Engagement" zLabel="Duración (s)" xFormat={axisNumber} yFormat={axisPct} zFormat={(v) => clock(v)} />
            </ChartCard>
          </div>
          {selected && <OrganicVideoPanel video={selected} onClose={() => setSelected(null)} />}
        </>
      )}
    </OrganicPage>
  )
}
