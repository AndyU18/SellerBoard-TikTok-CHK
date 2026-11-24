import { BadgeCheck } from 'lucide-react'
import { useState } from 'react'

import { Kpi } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisNumber, ChartCard, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent } from '@/components/ui/card'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokOrganicData, type OrganicAccount, type OrganicGrowth, type OrganicOverview, type OrganicPerformance, type OrganicVideo } from '@/lib/tiktokOrganicData'
import { useAsync } from '@/lib/useAsync'

import { OrganicVideoPanel } from './OrganicVideoPanel'
import { avgViews, longDate, pct, shortDate, signed } from './tiktokOrganicHelpers'
import { OrganicPage, TrackingNote, VideoCover, VideoRankList, type OrganicTabProps } from './tiktokOrganicUi'

const TOP_VIDEOS = 5
const GROWTH_WINDOWS = [7, 30]

interface OverviewData {
  overview: OrganicOverview
  performance: OrganicPerformance
}

const FOLLOWERS_SERIES: ComboSeries[] = [{ key: 'followers', label: 'Seguidores', type: 'line', color: '#ec4899', format: count }]
const VIEWS_SERIES: ComboSeries[] = [
  { key: 'views_gained', label: 'Vistas ganadas por día', type: 'bar', color: '#3b82f6', format: count },
  { key: 'views_total', label: 'Vistas acumuladas', type: 'line', axis: 'right', color: '#10b981', format: count },
]

function growthText(growth: OrganicGrowth | null | undefined): string {
  return growth ? `${signed(growth.delta)}${growth.pct == null ? '' : ` (${growth.pct > 0 ? '+' : ''}${growth.pct.toFixed(1)}%)`}` : 'sin historial'
}

function ProfileCard({ account }: { account: OrganicAccount }) {
  const name = account.display_name || account.username || 'Cuenta de TikTok'
  return (
    <Card>
      <CardContent className="flex items-start gap-4">
        <VideoCover url={account.avatar_url} alt={`Foto de ${name}`} className="size-16 shrink-0 rounded-full" />
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">{name}</h2>
            {account.is_verified && <BadgeCheck className="size-4 text-blue-600" aria-label="Cuenta verificada" />}
          </div>
          {account.username &&
            (account.profile_url ? (
              <a href={account.profile_url} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-700 hover:underline dark:text-blue-400">
                @{account.username}
              </a>
            ) : (
              <span className="text-muted-foreground text-sm">@{account.username}</span>
            ))}
          {account.bio && <p className="text-sm whitespace-pre-line">{account.bio}</p>}
          <p className="text-muted-foreground text-xs">Última foto de la cuenta: {longDate(account.snapshot_date)}</p>
        </div>
      </CardContent>
    </Card>
  )
}

function KpiGrid({ o }: { o: OrganicOverview }) {
  const growthByDays = new Map(o.growth.map((g) => [g.days, g.followers]))
  const followersHint = GROWTH_WINDOWS.map((d) => `${d} d: ${growthText(growthByDays.get(d))}`).join(' · ')
  const lastPost = o.posting.days_since_last_post
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi title="Seguidores" value={count(o.followers)} hint={followersHint} />
      <Kpi title="Siguiendo" value={count(o.following)} />
      <Kpi title="Likes totales de la cuenta" value={count(o.likes_total)} />
      <Kpi title="Videos" value={count(o.videos_tracked)} hint={o.videos_reported == null ? undefined : `${count(o.videos_reported)} reportados por TikTok`} />
      <Kpi title="Vistas totales" value={count(o.total_views)} hint={`${count(o.total_likes)} likes · ${count(o.total_comments)} comentarios · ${count(o.total_shares)} compartidos`} />
      <Kpi title="Engagement" value={pct(o.engagement)} hint="(likes + comentarios + compartidos) / vistas" />
      <Kpi title="Vistas: mediana y promedio" value={avgViews(o.median_views)} hint={`promedio ${avgViews(o.avg_views)} por video`} />
      <Kpi
        title="Publicaciones por semana"
        value={o.posting.posts_per_week.toFixed(1)}
        hint={`${count(o.posting.videos_published)} videos en ${o.posting.days} d${lastPost == null ? '' : ` · último hace ${lastPost} d`}`}
      />
    </div>
  )
}

function TopVideos({ videos, onSelect }: { videos: OrganicVideo[]; onSelect: (v: OrganicVideo) => void }) {
  return (
    <ChartCard title="Tus 5 mejores videos" subtitle="Por vistas, dentro del período elegido. Un clic abre el detalle." filename="tiktok-organico-mejores-videos">
      {videos.length ? <VideoRankList videos={videos} onSelect={onSelect} /> : <EmptyChart>Sin videos publicados en el período.</EmptyChart>}
    </ChartCard>
  )
}

/** Resumen de la cuenta orgánica de TikTok: perfil, indicadores, series de seguidores y vistas y mejores videos. */
export function TikTokOrganicOverview({ onOpenTab }: OrganicTabProps) {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const state = useAsync<OverviewData>(async () => {
    const [overview, performance] = await Promise.all([tiktokOrganicData.overview(days), tiktokOrganicData.performance(days)])
    return { overview, performance }
  }, [days])
  const [selected, setSelected] = useState<OrganicVideo | null>(null)

  return (
    <OrganicPage
      title="TikTok orgánico · Resumen"
      note="La cuenta de contenido propia de CHK (no TikTok Shop)"
      state={state}
      isEmpty={(d) => !d.overview.has_data}
      onOpenTab={onOpenTab}
    >
      {({ overview, performance }) => (
        <>
          <TrackingNote since={performance.tracking_since} />
          {overview.account && <ProfileCard account={overview.account} />}
          <KpiGrid o={overview} />
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Seguidores"
              subtitle="Una foto por día desde la conexión"
              filename="tiktok-organico-seguidores"
              csv={performance.followers_series.map((p) => ({ Fecha: p.date, Seguidores: p.followers }))}
            >
              <ComboChart data={performance.followers_series.map((p) => ({ ...p }))} xKey="date" series={FOLLOWERS_SERIES} height={260} xFormat={shortDate} leftFormat={axisNumber} />
            </ChartCard>
            <ChartCard
              title="Vistas"
              subtitle="Vistas ganadas entre fotos y total acumulado de todos los videos"
              filename="tiktok-organico-vistas"
              csv={performance.views_series.map((p) => ({ Fecha: p.date, 'Vistas totales': p.views_total, 'Vistas ganadas': p.views_gained }))}
            >
              <ComboChart data={performance.views_series.map((p) => ({ ...p }))} xKey="date" series={VIEWS_SERIES} height={260} xFormat={shortDate} leftFormat={axisNumber} rightFormat={axisNumber} />
            </ChartCard>
          </div>
          <TopVideos videos={performance.top_videos.slice(0, TOP_VIDEOS)} onSelect={setSelected} />
          {selected && <OrganicVideoPanel video={selected} onClose={() => setSelected(null)} />}
        </>
      )}
    </OrganicPage>
  )
}
