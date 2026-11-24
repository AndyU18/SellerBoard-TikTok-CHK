import { useMemo, useState } from 'react'

import type { PromoActivity } from '@/lib/tiktokPromos'

import { GROUP_LABEL, GROUP_STYLE, statusStyle, type PromoGroup } from './promoStatus'
import { dateRange, toMs } from './tiktokFormat'

const DAY_MS = 86_400_000
const MIN_BAR_PERCENT = 1.5
const OPEN_END_PADDING_DAYS = 7
const LEGEND_GROUPS: PromoGroup[] = ['active', 'scheduled', 'ended']

interface Bar {
  activity: PromoActivity
  start: number
  end: number
  openEnded: boolean
}

function buildBars(activities: PromoActivity[], now: number): Bar[] {
  return activities
    .flatMap((activity) => {
      const start = toMs(activity.begin_at)
      if (start == null) return []
      const end = toMs(activity.end_at)
      return [{ activity, start, end: end ?? Math.max(now, start) + OPEN_END_PADDING_DAYS * DAY_MS, openEnded: end == null }]
    })
    .sort((a, b) => a.start - b.start)
}

/** Línea de tiempo simple: una barra horizontal por actividad, de su inicio a su fin, coloreada por estado. */
export function PromoGantt({
  activities,
  selectedId,
  onSelect,
}: {
  activities: PromoActivity[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const [now] = useState(() => Date.now())
  const bars = useMemo(() => buildBars(activities, now), [activities, now])
  if (!bars.length) return <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">Ninguna actividad tiene fecha de inicio.</div>

  const min = Math.min(...bars.map((b) => b.start))
  const max = Math.max(...bars.map((b) => b.end), now)
  const span = Math.max(max - min, DAY_MS)
  const pos = (t: number) => ((t - min) / span) * 100
  const todayLeft = pos(now)

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {LEGEND_GROUPS.map((g) => (
          <span key={g} className="flex items-center gap-1.5">
            <span className="inline-block size-3 rounded-sm" style={{ background: GROUP_STYLE[g].bar }} />
            {GROUP_LABEL[g]}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-px bg-slate-500" /> Hoy
        </span>
      </div>
      <div className="max-h-96 overflow-auto rounded-md border">
        <ul className="min-w-xl">
          {bars.map(({ activity, start, end, openEnded }) => {
            const left = pos(start)
            const width = Math.max(pos(end) - left, MIN_BAR_PERCENT)
            const style = statusStyle(activity.status)
            const name = activity.title ?? activity.activity_id
            const range = `${dateRange(activity.begin_at, activity.end_at)}${openEnded ? ' (sin fecha de fin)' : ''}`
            const selected = selectedId === activity.activity_id
            return (
              <li key={activity.activity_id} className="grid grid-cols-[10rem_1fr] items-center gap-2 border-b px-2 py-1 last:border-b-0 sm:grid-cols-[14rem_1fr]">
                <span className="truncate text-xs" title={name}>
                  {name}
                </span>
                <div className="relative h-6">
                  <span aria-hidden className="absolute inset-y-0 w-px bg-slate-400" style={{ left: `${todayLeft}%` }} />
                  <button
                    type="button"
                    onClick={() => onSelect(activity.activity_id)}
                    aria-pressed={selected}
                    aria-label={`${name}: ${style.label}, ${range}`}
                    title={`${name} · ${style.label} · ${range}`}
                    className={`absolute inset-y-1 rounded-sm focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:outline-none ${openEnded ? 'border-r-2 border-dashed border-white' : ''}`}
                    style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%`, background: style.bar, opacity: selectedId && !selected ? 0.45 : 1 }}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      </div>
      <p className="text-muted-foreground mt-1 text-xs">
        {new Date(min).toLocaleDateString('es-US', { dateStyle: 'medium' })} → {new Date(max).toLocaleDateString('es-US', { dateStyle: 'medium' })}. Haz clic en una barra para ver sus productos.
      </p>
    </div>
  )
}
