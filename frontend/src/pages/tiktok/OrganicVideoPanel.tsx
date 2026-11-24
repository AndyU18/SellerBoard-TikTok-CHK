import { ExternalLink, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import type { OrganicVideo, OrganicVideoListItem } from '@/lib/tiktokOrganicData'
import { count } from '@/lib/format'

import { clock, longDate, pct, signed, videoTitle } from './tiktokOrganicHelpers'
import { VideoCover } from './tiktokOrganicUi'

type PanelVideo = OrganicVideo | OrganicVideoListItem

const hasGrowth = (v: PanelVideo): v is OrganicVideoListItem => 'recent_growth' in v

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border p-2">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  )
}

/** Panel lateral con todas las métricas de un video. Se cierra con Escape, el fondo o el botón. */
export function OrganicVideoPanel({ video, onClose }: { video: PanelVideo; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const title = videoTitle(video)
  const growth = hasGrowth(video) ? video.recent_growth : null

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Detalle del video: ${title}`}
        onClick={(e) => e.stopPropagation()}
        className="bg-background h-full w-full max-w-md space-y-4 overflow-y-auto p-4 shadow-xl"
      >
        <div className="flex items-start justify-between gap-2">
          <h2 className="text-base font-semibold">{title}</h2>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Cerrar" className="rounded p-1 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none dark:hover:bg-slate-800">
            <X className="size-5" aria-hidden />
          </button>
        </div>
        <VideoCover url={video.cover_url} alt={title} className="mx-auto aspect-[9/16] max-h-80 rounded-md" />
        <p className="text-muted-foreground text-xs">
          Publicado el {longDate(video.created_at)}
          {video.age_days != null && ` (hace ${video.age_days} d)`} · duración {clock(video.duration_seconds)}
        </p>
        <dl className="grid grid-cols-2 gap-2">
          <Metric label="Vistas" value={count(video.views)} />
          <Metric label="Likes" value={count(video.likes)} />
          <Metric label="Comentarios" value={count(video.comments)} />
          <Metric label="Compartidos" value={count(video.shares)} />
          <Metric label="Engagement" value={pct(video.engagement)} />
          <Metric label="Vistas por día" value={count(Math.round(video.views_per_day))} />
          {hasGrowth(video) && <Metric label={`Vistas ganadas (${growth?.days ?? 7} d)`} value={growth ? signed(growth.delta) : '—'} />}
        </dl>
        {video.description && video.description !== video.title && <p className="text-sm whitespace-pre-line">{video.description}</p>}
        {video.share_url && (
          <a href={video.share_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-sm font-medium text-blue-700 hover:underline dark:text-blue-400">
            <ExternalLink className="size-4" aria-hidden /> Abrir en TikTok
          </a>
        )}
      </aside>
    </div>
  )
}
