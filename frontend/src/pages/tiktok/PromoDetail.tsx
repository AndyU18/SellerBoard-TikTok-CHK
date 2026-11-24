import { X } from 'lucide-react'

import { Callout, pct1, Pill } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import type { PromoActivity } from '@/lib/tiktokPromos'

import { statusStyle } from './promoStatus'
import { dateRange, humanize, NO_VALUE } from './tiktokFormat'

const OBSERVATIONAL_NOTE =
  'El ROI es observacional: compara unidades por día durante la actividad contra los días previos y no separa el efecto de la temporada, la publicidad ni los creadores.'
const SHOP_LEVEL_NOTE =
  'Las actividades de nivel tienda no traen productos: su ROI se mide contra todas las ventas de TikTok Shop.'

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  )
}

/** Panel con el detalle de una actividad: ROI, productos incluidos y avisos de lectura. */
export function PromoDetail({ activity, onClose }: { activity: PromoActivity; onClose: () => void }) {
  const style = statusStyle(activity.status)
  const { roi } = activity
  const shopWide = activity.roi_scope === 'shop'
  return (
    <Card role="region" aria-label={`Detalle de ${activity.title ?? activity.activity_id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle className="text-base">{activity.title ?? activity.activity_id}</CardTitle>
          <p className="text-muted-foreground text-xs">
            {humanize(activity.type)} · {dateRange(activity.begin_at, activity.end_at)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Pill label={style.label} className={style.pill} />
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar detalle"
            className="text-muted-foreground hover:text-foreground rounded p-1.5 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {roi ? (
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Fact label="Aumento de unidades/día" value={pct1(roi.uplift)} />
            <Fact label={`Unidades/día durante (${count(roi.days_running)} d)`} value={roi.units_per_day_during.toFixed(2)} />
            <Fact label="Unidades/día antes" value={roi.units_per_day_before.toFixed(2)} />
            <Fact label="Ventas durante" value={money(roi.sales_during)} />
          </dl>
        ) : (
          <p className="text-muted-foreground text-sm">Sin ROI: la actividad aún no empieza o no se pudo vincular a productos.</p>
        )}

        <Callout tone="warning">{OBSERVATIONAL_NOTE}</Callout>
        {shopWide && <Callout tone="info">{SHOP_LEVEL_NOTE}</Callout>}

        {activity.products.length > 0 ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Producto</TableHead>
                  <TableHead className="text-right">Precio de actividad</TableHead>
                  <TableHead>Descuento</TableHead>
                  <TableHead className="text-right">Límite</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {activity.products.map((p) => (
                  <TableRow key={p.product_id}>
                    <TableCell className="max-w-xs truncate font-medium" title={p.title ?? p.product_id}>
                      {p.title ?? p.product_id}
                    </TableCell>
                    <TableCell className="text-right">{money(p.activity_price)}</TableCell>
                    <TableCell>{p.discount ?? NO_VALUE}</TableCell>
                    <TableCell className="text-right">{p.quantity_limit == null ? 'Sin límite' : count(p.quantity_limit)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {(activity.product_count ?? 0) > activity.products.length && (
              <p className="text-muted-foreground mt-2 text-xs">
                Se muestran {count(activity.products.length)} de {count(activity.product_count)} productos.
              </p>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">{shopWide ? 'Actividad de nivel tienda: no lista productos.' : 'No hay productos vinculados.'}</p>
        )}
      </CardContent>
    </Card>
  )
}
