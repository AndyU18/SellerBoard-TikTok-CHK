import { useMemo, useState } from 'react'

import { Callout, Pill, PILL } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisNumber, ChartCard } from '@/components/charts/kit'
import { HeatGrid } from '@/components/charts/HeatGrid'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokOrganicData, type OrganicPostingTimes } from '@/lib/tiktokOrganicData'
import { useAsync } from '@/lib/useAsync'

import { avgViews, useTrackingSince } from './tiktokOrganicHelpers'
import { OrganicPage, TrackingNote, type OrganicTabProps } from './tiktokOrganicUi'

const HOURS = Array.from({ length: 24 }, (_, h) => h)
const HOUR_LABELS = HOURS.map(String)
const hourLabel = (h: number) => `${String(h).padStart(2, '0')}:00`

const BAR_SERIES: ComboSeries[] = [
  { key: 'avg_views', label: 'Vistas medias', type: 'bar', color: '#3b82f6', format: avgViews },
  { key: 'videos', label: 'Videos publicados', type: 'line', axis: 'right', color: '#f59e0b', format: count },
]

interface CellSelection {
  weekday: number
  hour: number
}

function CellDetail({ data, cell }: { data: OrganicPostingTimes; cell: CellSelection | null }) {
  if (!cell) return null
  const stats = data.heatmap[cell.weekday]?.[cell.hour]
  return (
    <p className="text-sm" role="status">
      <strong>
        {data.weekday_names[cell.weekday]} {hourLabel(cell.hour)}
      </strong>
      : {stats?.videos ? `${count(stats.videos)} ${stats.videos === 1 ? 'video' : 'videos'} · ${avgViews(stats.avg_views)} vistas medias` : 'sin publicaciones en esta franja'}
    </p>
  )
}

function BestSlots({ slots }: { slots: OrganicPostingTimes['best_slots'] }) {
  if (!slots.length) return <p className="text-muted-foreground text-sm">Todavía no hay franjas con videos para recomendar.</p>
  return (
    <ol className="space-y-2">
      {slots.map((s, i) => (
        <li key={`${s.weekday}-${s.hour}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2 text-sm">
          <span>
            <span className="text-muted-foreground mr-2 tabular-nums">{i + 1}.</span>
            <strong>
              {s.weekday_name} {hourLabel(s.hour)}
            </strong>
          </span>
          <span className="flex flex-wrap items-center gap-2">
            <span className="tabular-nums">{avgViews(s.avg_views)} vistas medias</span>
            <span className="text-muted-foreground text-xs">
              {count(s.videos)} {s.videos === 1 ? 'video' : 'videos'}
            </span>
            {s.low_sample && <Pill label="Muestra baja" className={PILL.amber} />}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** Mejores horarios para publicar: mapa de calor día × hora, mejores franjas y barras por día y por hora. */
export function TikTokOrganicTimes({ onOpenTab }: OrganicTabProps) {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const state = useAsync(() => tiktokOrganicData.postingTimes(days), [days])
  const since = useTrackingSince()
  const [cell, setCell] = useState<CellSelection | null>(null)
  const d = state.data
  const values = useMemo(() => (d?.heatmap ?? []).map((row) => row.map((c) => c.avg_views ?? 0)), [d])
  const lowSampleShown = d?.best_slots.some((s) => s.low_sample) ?? false

  return (
    <OrganicPage
      title="TikTok orgánico · Mejores horarios para publicar"
      note={d ? `${count(d.videos)} videos publicados en los últimos ${d.days} días · zona horaria ${d.timezone}` : undefined}
      state={state}
      isEmpty={(x) => x.videos === 0}
      scopedToPeriod
      onOpenTab={onOpenTab}
    >
      {(x) => (
        <>
          <TrackingNote since={since} />
          {lowSampleShown && <Callout tone="warning">Hay franjas con pocos videos (menos de 3): úsalas como pista, no como regla. Con más publicaciones el resultado se vuelve más confiable.</Callout>}
          <div className="grid gap-4 xl:grid-cols-3">
            <ChartCard title="Mejores franjas" subtitle="Día y hora de publicación con más vistas medias" filename="tiktok-organico-mejores-franjas" csv={x.best_slots.map((s) => ({ Día: s.weekday_name, Hora: hourLabel(s.hour), 'Vistas medias': s.avg_views, Videos: s.videos, 'Muestra baja': s.low_sample ? 'sí' : 'no' }))}>
              <BestSlots slots={x.best_slots} />
            </ChartCard>
            <ChartCard
              title="Mapa de calor: vistas medias por día y hora"
              subtitle={`Hora de publicación en ${x.timezone}. Más oscuro = más vistas medias; las celdas vacías no tienen publicaciones. Un clic muestra el detalle.`}
              filename="tiktok-organico-mapa-horarios"
              className="xl:col-span-2"
            >
              <HeatGrid
                rowLabels={x.weekday_names}
                colLabels={HOUR_LABELS}
                values={values}
                format={(v) => count(Math.round(v))}
                unit="vistas medias"
                onCellClick={(weekday, hour) => setCell({ weekday, hour })}
              />
              <CellDetail data={x} cell={cell} />
            </ChartCard>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="Por día de la semana" subtitle="Vistas medias y cantidad de videos" filename="tiktok-organico-por-dia" csv={x.by_weekday.map((r) => ({ Día: r.name, 'Vistas medias': r.avg_views, Videos: r.videos }))}>
              <ComboChart data={x.by_weekday.map((r) => ({ ...r }))} xKey="name" series={BAR_SERIES} height={260} leftFormat={axisNumber} rightFormat={axisNumber} />
            </ChartCard>
            <ChartCard title="Por hora del día" subtitle={`Vistas medias y cantidad de videos (${x.timezone})`} filename="tiktok-organico-por-hora" csv={x.by_hour.map((r) => ({ Hora: hourLabel(r.hour), 'Vistas medias': r.avg_views, Videos: r.videos }))}>
              <ComboChart data={x.by_hour.map((r) => ({ ...r }))} xKey="hour" series={BAR_SERIES} height={260} xFormat={(v) => hourLabel(Number(v))} leftFormat={axisNumber} rightFormat={axisNumber} />
            </ChartCard>
          </div>
        </>
      )}
    </OrganicPage>
  )
}
