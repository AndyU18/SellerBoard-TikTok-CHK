// Piezas compartidas por las pantallas de TikTok orgánico: marco de página con estados, aviso de historial,
// portada con marcador, lista de videos y formatos.
import { ImageOff, Link2, Play } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Callout, ErrorAlert, Hint } from '@/components/analytics/ui'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { count } from '@/lib/format'
import { tiktokOrganic } from '@/lib/tiktokOrganic'
import type { OrganicVideo } from '@/lib/tiktokOrganicData'
import { useAsync, type AsyncState } from '@/lib/useAsync'

import { PageTitle } from '../walmart/walmartUi'
import { CONNECTION_TAB, longDate, pct, videoTitle } from './tiktokOrganicHelpers'

/** Propiedades comunes de todas las pantallas: `onOpenTab('organicConnection')` lleva a la pantalla de Conexión. */
export interface OrganicTabProps {
  onOpenTab?: (tab: string) => void
}

const LIMITS_NOTE =
  'TikTok solo entrega las cifras actuales de cada video y de la cuenta: el historial (crecimiento, series) se arma con una foto diaria desde que se conectó la cuenta. ' +
  'La API no da demografía de la audiencia, impresiones, alcance ni retención.'

export function TrackingNote({ since }: { since: string | null }) {
  return (
    <Callout tone="info">
      {since ? <>Historial desde el <strong>{longDate(since)}</strong> (cuando se conectó la cuenta). </> : <>Aún no hay historial guardado. </>}
      {LIMITS_NOTE}
    </Callout>
  )
}

/** Estado amable cuando la cuenta no está conectada o todavía no hay datos. */
export function NoDataState({ onOpenTab, scopedToPeriod = false }: OrganicTabProps & { scopedToPeriod?: boolean }) {
  const status = useAsync(() => tiktokOrganic.status(), [])
  const s = status.data
  const connected = !!s?.connected && !s.needs_reauthorization
  const waiting = connected && !s?.last_snapshot_date
  const message = !connected
    ? 'Conecta la cuenta de TikTok de CHK para empezar a ver seguidores, videos y su rendimiento. Se hace una sola vez y es de solo lectura.'
    : waiting
      ? 'La cuenta ya está conectada. La primera sincronización guarda tus videos; en cuanto termine verás los datos aquí.'
      : scopedToPeriod
        ? 'No hay videos publicados en el período elegido. Prueba con un período más largo en los filtros de arriba.'
        : 'Todavía no hay videos sincronizados. Vuelve en un rato.'
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
        <Play className="text-muted-foreground size-8" aria-hidden />
        <h2 className="text-lg font-semibold">{connected ? 'Aún no hay datos para mostrar' : 'Tu cuenta de TikTok no está conectada'}</h2>
        <p className="text-muted-foreground max-w-xl text-sm">{message}</p>
        {!status.loading && !connected && onOpenTab && (
          <Button type="button" onClick={() => onOpenTab(CONNECTION_TAB)}>
            <Link2 className="size-4" /> Ir a Conexión
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Marco de cada pantalla: título con el alcance, error, carga, estado sin datos y la nota de límites.
 * `children` solo se pinta con datos (`isEmpty` falso).
 */
export function OrganicPage<T>({
  title,
  note,
  state,
  isEmpty,
  scopedToPeriod,
  onOpenTab,
  children,
}: OrganicTabProps & {
  title: string
  note?: string
  state: AsyncState<T>
  isEmpty: (data: T) => boolean
  scopedToPeriod?: boolean
  children: (data: T) => ReactNode
}) {
  const scope = useScopeDescription()
  const data = state.data
  return (
    <div className="space-y-6">
      <PageTitle title={title} scope={scope} note={note} />
      <ErrorAlert message={state.error} />
      {state.loading && !data && <div className="text-muted-foreground text-sm">Cargando…</div>}
      {data && (isEmpty(data) ? <NoDataState onOpenTab={onOpenTab} scopedToPeriod={scopedToPeriod} /> : children(data))}
      <Hint>{LIMITS_NOTE}</Hint>
    </div>
  )
}

/** Portada del video. Las portadas de TikTok caducan en unas 6 horas: si falla, se muestra un marcador. */
export function VideoCover({ url, alt, className = '' }: { url: string | null; alt: string; className?: string }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  if (!url || failedUrl === url) {
    return (
      <div role="img" aria-label="Portada no disponible" title="La portada caducó; se renueva en la próxima sincronización" className={`bg-muted text-muted-foreground flex items-center justify-center ${className}`}>
        <ImageOff className="size-6" aria-hidden />
      </div>
    )
  }
  return <img src={url} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailedUrl(url)} className={`object-cover ${className}`} />
}

/** Lista de videos con portada y cifras clave; un clic en una fila avisa con `onSelect`. */
export function VideoRankList({ videos, onSelect }: { videos: OrganicVideo[]; onSelect: (video: OrganicVideo) => void }) {
  return (
    <ol className="divide-y">
      {videos.map((v, i) => (
        <li key={v.video_id}>
          <button type="button" onClick={() => onSelect(v)} className="flex w-full items-center gap-3 rounded p-2 text-left hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none dark:hover:bg-slate-900">
            <span className="text-muted-foreground w-5 shrink-0 text-center text-sm tabular-nums">{i + 1}</span>
            <VideoCover url={v.cover_url} alt={videoTitle(v)} className="h-16 w-12 shrink-0 rounded" />
            <span className="min-w-0 flex-1">
              <span className="line-clamp-2 text-sm font-medium">{videoTitle(v)}</span>
              <span className="text-muted-foreground block text-xs">
                {count(v.views)} vistas · {count(v.likes)} likes · engagement {pct(v.engagement)}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ol>
  )
}
