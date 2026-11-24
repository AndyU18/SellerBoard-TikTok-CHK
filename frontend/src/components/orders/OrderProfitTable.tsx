import { pct1 } from '@/components/analytics/ui'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { money } from '@/lib/format'
import type { OrderDriverKey, OrderProfitRow } from '@/lib/orderProfit'

const DRIVER_STYLE: Record<OrderDriverKey, string> = {
  cogs: 'bg-amber-100 text-amber-800',
  fees: 'bg-violet-100 text-violet-800',
  shipping: 'bg-blue-100 text-blue-800',
  refunds: 'bg-red-100 text-red-800',
  promo: 'bg-pink-100 text-pink-800',
}

const COLUMN_COUNT = 12
const num = 'text-right whitespace-nowrap tabular-nums'

/** Tabla de órdenes de un solo canal con su utilidad y la causa principal de la pérdida. */
export function OrderProfitTable({
  rows,
  loading,
  emptyText,
}: {
  rows: OrderProfitRow[]
  loading: boolean
  emptyText: string
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Fecha</TableHead>
          <TableHead>Orden</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead className="text-right">Un.</TableHead>
          <TableHead className="text-right">Ventas</TableHead>
          <TableHead className="text-right">Comisiones</TableHead>
          <TableHead className="text-right">Envío</TableHead>
          <TableHead className="text-right">Reembolsos</TableHead>
          <TableHead className="text-right">COGS</TableHead>
          <TableHead className="text-right">Utilidad</TableHead>
          <TableHead className="text-right">Margen</TableHead>
          <TableHead>Causa principal</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={COLUMN_COUNT} className="text-muted-foreground py-10 text-center">
              {loading ? 'Cargando…' : emptyText}
            </TableCell>
          </TableRow>
        )}
        {rows.map((r) => (
          <TableRow key={`${r.account}:${r.order}`}>
            <TableCell className="whitespace-nowrap">{r.date}</TableCell>
            <TableCell className="font-mono text-xs">{r.order}</TableCell>
            <TableCell>{r.state ?? '—'}</TableCell>
            <TableCell className="text-right tabular-nums">{r.units}</TableCell>
            <TableCell className={num}>{money(r.net_sales)}</TableCell>
            <TableCell className={num}>{money(r.fees)}</TableCell>
            <TableCell className={num} title={r.label_cost ? `Incluye etiqueta Redo ${money(r.label_cost)}` : undefined}>
              {money(r.shipping)}
            </TableCell>
            <TableCell className={num}>{money(r.refunds)}</TableCell>
            <TableCell className={num}>{money(r.cogs)}</TableCell>
            <TableCell className={`${num} font-semibold ${r.profit < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
              {money(r.profit)}
            </TableCell>
            <TableCell className="text-right tabular-nums">{pct1(r.margin)}</TableCell>
            <TableCell>
              {r.driver ? (
                <span
                  title={r.driver_label ?? undefined}
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${DRIVER_STYLE[r.driver]}`}
                >
                  {r.driver_short}
                </span>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
