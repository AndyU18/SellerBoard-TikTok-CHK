import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'

import { ErrorAlert } from '@/components/analytics/ui'
import { ChannelBadge } from '@/components/ChannelBadge'
import { useScopeDescription } from '@/components/PageHeader'
import { ChartCard, type CsvRow } from '@/components/charts/kit'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { Filters } from '@/lib/api'
import { orderLines } from '@/lib/chartdataPedidos'
import { count, money, neg, tone } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const PAGE = 25

/** Una fila por línea de pedido, paginada en el servidor, con búsqueda por orden, SKU o nombre. */
export function OrderLinesTable({ filters, tabs }: { filters: Filters; tabs: ReactNode }) {
  const description = useScopeDescription()
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)

  // Espera a que el usuario deje de escribir antes de consultar.
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(text.trim())
      setPage(0)
    }, 300)
    return () => clearTimeout(t)
  }, [text])
  // Al cambiar el alcance global se vuelve a la primera página.
  const scopeKey = [filters.start, filters.end, filters.channel, filters.account].join('|')
  const [seenScope, setSeenScope] = useState(scopeKey)
  if (seenScope !== scopeKey) {
    setSeenScope(scopeKey)
    setPage(0)
  }

  const data = useAsync(() => orderLines(filters, q, PAGE, page * PAGE), [filters.start, filters.end, filters.channel, filters.account, q, page])
  const rows = data.data?.rows ?? []
  const total = data.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  const csv: CsvRow[] = rows.map((r) => ({
    orden: r.order,
    fecha: r.date,
    canal: r.channel,
    tienda: r.account,
    sku: r.sku,
    producto: r.product_name,
    unidades: r.units,
    ventas: r.gross_sales,
    promo: r.promo,
    comisiones: r.platform_fees,
    envio: r.shipping_cost,
    reembolso: r.refund_amount,
    costo: r.cogs,
    ganancia: r.net_profit,
  }))
  const s = data.data?.summary

  return (
    <ChartCard
      title={`Artículos del pedido — ${description}`}
      subtitle={
        s
          ? `${count(s.lines)} líneas · ${count(s.units)} unidades · ventas ${money(s.gross_sales)} · ganancia ${money(s.net_profit)} (antes de publicidad). El CSV baja la página visible.`
          : 'Una fila por producto dentro de cada orden.'
      }
      csv={csv}
      filename="dashboard-articulos-del-pedido"
      actions={tabs}
    >
      <div className="relative mb-3 max-w-sm">
        <Search className="text-muted-foreground absolute top-2 left-2 size-4" />
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Buscar orden, SKU o producto…"
          aria-label="Buscar artículos del pedido"
          className="pl-8"
        />
      </div>
      <ErrorAlert message={data.error} />
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              {['Orden', 'Fecha', 'Canal', 'Tienda', 'Producto'].map((h) => (
                <TableHead key={h}>{h}</TableHead>
              ))}
              {['Unid.', 'Ventas', 'Comisiones', 'Envío', 'Reembolso', 'Costo', 'Ganancia'].map((h) => (
                <TableHead key={h} className="text-right">
                  {h}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={12} className="text-muted-foreground py-10 text-center">
                  {data.loading ? 'Cargando…' : q ? `Sin resultados para "${q}".` : 'Sin pedidos para este filtro.'}
                </TableCell>
              </TableRow>
            )}
            {rows.map((r) => (
              <TableRow key={r.id}>
                <TableCell className="font-mono text-xs">{r.order}</TableCell>
                <TableCell className="whitespace-nowrap">{r.date}</TableCell>
                <TableCell>
                  <ChannelBadge channel={r.channel} />
                </TableCell>
                <TableCell className="text-xs">{r.account}</TableCell>
                <TableCell>
                  <div className="min-w-44">
                    <div className="font-medium">{r.product_name ?? r.sku}</div>
                    <div className="text-muted-foreground text-xs">{r.sku}</div>
                  </div>
                </TableCell>
                <TableCell className="text-right">{count(r.units)}</TableCell>
                <TableCell className="text-right">{money(r.gross_sales)}</TableCell>
                <TableCell className="text-right">{money(neg(r.platform_fees))}</TableCell>
                <TableCell className="text-right">{money(neg(r.shipping_cost))}</TableCell>
                <TableCell className="text-right">{money(neg(r.refund_amount))}</TableCell>
                <TableCell className="text-right">{money(neg(r.cogs))}</TableCell>
                <TableCell className={`text-right font-semibold ${tone(r.net_profit)}`}>{money(r.net_profit)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="mt-3 flex items-center justify-between text-xs">
        <span className="text-muted-foreground">
          Página {page + 1} de {pages}
        </span>
        <div className="flex gap-1">
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
            aria-label="Página anterior"
            className="rounded border p-1.5 hover:bg-slate-100 disabled:opacity-40"
          >
            <ChevronLeft className="size-4" />
          </button>
          <button
            type="button"
            disabled={page + 1 >= pages}
            onClick={() => setPage(page + 1)}
            aria-label="Página siguiente"
            className="rounded border p-1.5 hover:bg-slate-100 disabled:opacity-40"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      </div>
    </ChartCard>
  )
}
