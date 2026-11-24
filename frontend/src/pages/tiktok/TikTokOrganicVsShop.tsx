import { Callout, Hint, Kpi } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokOrganicData, type OrganicVsShop } from '@/lib/tiktokOrganicData'
import { useAsync } from '@/lib/useAsync'

import { CsvButton } from '../walmart/walmartUi'
import { useTrackingSince, videoTitle } from './tiktokOrganicHelpers'
import { OrganicPage, TrackingNote, type OrganicTabProps } from './tiktokOrganicUi'

const LINK_STEPS = [
  'Al publicar un video en TikTok, agrega el enlace de producto de tu tienda (carrito amarillo) para el producto que aparece.',
  'Pide a los afiliados y creadores que usen tu enlace de producto: así la venta queda con el id del video.',
  'Vuelve aquí después de unos días: las ventas aparecerán asociadas a cada video orgánico cuando los ids coincidan.',
]

function Totals({ t, matched }: { t: OrganicVsShop['totals']; matched: number }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <Kpi title="Videos con ventas" value={count(matched)} hint="aparecen en las ventas de TikTok Shop" />
      <Kpi title="Ventas de la tienda" value={money(t.shop_gmv)} hint={`${count(t.shop_orders)} órdenes · ${count(t.shop_units)} unidades`} />
      <Kpi title="Ventas por afiliados" value={money(t.affiliate_sales)} hint={`${count(t.affiliate_orders)} órdenes · ${count(t.affiliate_units)} unidades`} />
      <Kpi title="Comisión de afiliados" value={money(t.affiliate_commission)} />
      <Kpi title="Vistas en la tienda" value={count(t.shop_views)} hint="vistas del video como contenido de la tienda" />
    </div>
  )
}

function NoMatches({ note }: { note: string }) {
  return (
    <>
      <Callout tone="info">{note}</Callout>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cómo lograr que tus videos aparezcan en las ventas</CardTitle>
        </CardHeader>
        <CardContent>
          <ol className="list-decimal space-y-2 pl-5 text-sm">
            {LINK_STEPS.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </>
  )
}

function MatchesTable({ videos }: { videos: OrganicVsShop['videos'] }) {
  const csv = videos.map((v) => ({
    Video: videoTitle({ title: v.title, description: null, video_id: v.video_id }),
    'Vistas orgánicas': v.organic_views,
    'Ventas tienda': v.shop_gmv,
    'Órdenes tienda': v.shop_orders,
    'Ventas afiliados': v.affiliate_sales,
    'Órdenes afiliados': v.affiliate_orders,
    Comisión: v.affiliate_commission,
    Enlace: v.share_url ?? '',
  }))
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Videos orgánicos con ventas</CardTitle>
        <CsvButton filename="tiktok-organico-vs-shop.csv" rows={csv} />
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Video</TableHead>
              <TableHead className="text-right">Vistas orgánicas</TableHead>
              <TableHead className="text-right">Ventas tienda</TableHead>
              <TableHead className="text-right">Órdenes tienda</TableHead>
              <TableHead className="text-right">Ventas afiliados</TableHead>
              <TableHead className="text-right">Órdenes afiliados</TableHead>
              <TableHead className="text-right">Comisión</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {videos.map((v) => {
              const title = videoTitle({ title: v.title, description: null, video_id: v.video_id })
              return (
                <TableRow key={v.video_id}>
                  <TableCell className="max-w-xs">
                    {v.share_url ? (
                      <a href={v.share_url} target="_blank" rel="noopener noreferrer" className="line-clamp-2 text-blue-700 hover:underline dark:text-blue-400">
                        {title}
                      </a>
                    ) : (
                      <span className="line-clamp-2">{title}</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{count(v.organic_views)}</TableCell>
                  <TableCell className="text-right font-medium">{money(v.shop_gmv)}</TableCell>
                  <TableCell className="text-right">{count(v.shop_orders)}</TableCell>
                  <TableCell className="text-right">{money(v.affiliate_sales)}</TableCell>
                  <TableCell className="text-right">{count(v.affiliate_orders)}</TableCell>
                  <TableCell className="text-right">{money(v.affiliate_commission)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

/** Cruce del TikTok orgánico con las ventas de TikTok Shop: qué videos propios aparecen en las ventas. */
export function TikTokOrganicVsShop({ onOpenTab }: OrganicTabProps) {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const state = useAsync(() => tiktokOrganicData.vsShop(days), [days])
  const since = useTrackingSince()

  return (
    <OrganicPage
      title="TikTok orgánico · Ventas en TikTok Shop"
      note="Qué videos de la cuenta orgánica generaron ventas en la tienda (se cruzan por el id del video)"
      state={state}
      isEmpty={(d) => d.videos_tracked === 0}
      onOpenTab={onOpenTab}
    >
      {(d) => (
        <>
          <TrackingNote since={since} />
          {d.matched_videos === 0 ? (
            <NoMatches note={d.note} />
          ) : (
            <>
              <Totals t={d.totals} matched={d.matched_videos} />
              <MatchesTable videos={d.videos} />
              <Hint>{d.note}</Hint>
            </>
          )}
        </>
      )}
    </OrganicPage>
  )
}
