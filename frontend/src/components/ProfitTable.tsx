import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { GroupBy, ProfitRow } from '@/lib/api'
import { count, money, neg, percent, tone } from '@/lib/format'

export interface CogsInfo {
  id: number
  cogs: number
}

interface Props {
  rows: ProfitRow[]
  totals: ProfitRow | null
  groupBy: GroupBy
  loading?: boolean
  /** Solo en vista por producto: permite editar el COGS unitario. */
  cogsBySku?: Record<string, CogsInfo>
  onCogsChange?: (productId: number, cogs: number) => void
}

/** Columna numérica. `color` pinta en rojo los negativos (solo utilidad, margen, ROI). */
const num = (
  get: (r: ProfitRow) => number | null,
  fmt: (v: number | null) => string,
  opts: { strong?: boolean; color?: boolean } = {},
) => ({
  accessorFn: get,
  cell: ({ getValue }: { getValue: () => unknown }) => {
    const v = getValue() as number | null
    return (
      <span className={`${opts.color ? tone(v) : ''} ${opts.strong ? 'font-semibold' : ''}`}>{fmt(v)}</span>
    )
  },
  meta: { align: 'right' },
})

const isRight = (meta: unknown) => (meta as { align?: string } | undefined)?.align === 'right'

export function ProfitTable({ rows, totals, groupBy, loading, cogsBySku, onCogsChange }: Props) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'net_profit', desc: true }])

  const columns = useMemo<ColumnDef<ProfitRow>[]>(() => {
    const label: ColumnDef<ProfitRow> = {
      id: 'label',
      header: groupBy === 'channel' ? 'Canal' : groupBy === 'account' ? 'Cuenta' : 'Producto',
      accessorFn: (r) => r.product_name ?? r.sku ?? r.account ?? r.channel ?? '',
      cell: ({ row }) => {
        const r = row.original
        if (r.sku)
          return (
            <div className="flex min-w-56 flex-col">
              <span className="font-medium">{r.product_name ?? r.sku}</span>
              <span className="text-muted-foreground text-xs">{r.sku}</span>
            </div>
          )
        return r.channel ? (
          <div className="flex flex-col">
            <ChannelBadge channel={r.channel} />
            {r.account && <span className="text-muted-foreground text-xs">{r.account}</span>}
          </div>
        ) : null
      },
    }
    const channelCol: ColumnDef<ProfitRow> = {
      id: 'channel',
      header: 'Canal',
      accessorFn: (r) => r.channel ?? '',
      cell: ({ row }) => (row.original.channel ? <ChannelBadge channel={row.original.channel} /> : null),
    }
    const cogsUnit: ColumnDef<ProfitRow> = {
      id: 'cogs_unit',
      header: 'COGS / unid.',
      enableSorting: false,
      meta: { align: 'right' },
      cell: ({ row }) => {
        const info = row.original.sku ? cogsBySku?.[row.original.sku] : undefined
        if (!info) return <span className="text-muted-foreground">—</span>
        return (
          <Input
            key={`${info.id}-${info.cogs}`}
            type="number"
            min={0}
            step="0.01"
            defaultValue={info.cogs}
            className="ml-auto h-8 w-24 text-right"
            aria-label={`COGS de ${row.original.sku}`}
            onBlur={(e) => {
              const v = Number(e.target.value)
              if (!Number.isNaN(v) && v >= 0 && v !== info.cogs) onCogsChange?.(info.id, v)
            }}
          />
        )
      },
    }

    return [
      label,
      ...(groupBy === 'channel_product' ? [channelCol] : []),
      { id: 'units', header: 'Unidades', ...num((r) => r.units, count) },
      { id: 'gross_sales', header: 'Ventas', ...num((r) => r.gross_sales, money) },
      { id: 'platform_fees', header: 'Comisiones', ...num((r) => neg(r.platform_fees), money) },
      { id: 'shipping_cost', header: 'Envío', ...num((r) => neg(r.shipping_cost), money) },
      { id: 'ad_spend', header: 'Publicidad', ...num((r) => neg(r.ad_spend), money) },
      { id: 'refund_amount', header: 'Reembolsos', ...num((r) => neg(r.refund_amount), money) },
      { id: 'cogs', header: 'COGS', ...num((r) => neg(r.cogs), money) },
      ...(groupBy === 'product' && cogsBySku ? [cogsUnit] : []),
      {
        id: 'net_profit',
        header: 'Utilidad neta',
        ...num((r) => r.net_profit, money, { strong: true, color: true }),
      },
      {
        id: 'unit_profit',
        header: 'Utilidad / unid.',
        ...num((r) => r.unit_profit, money, { strong: true, color: true }),
      },
      { id: 'margin', header: 'Margen', ...num((r) => r.margin, percent, { color: true }) },
      { id: 'roi', header: 'ROI', ...num((r) => r.roi, percent, { color: true }) },
    ]
  }, [groupBy, cogsBySku, onCogsChange])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  const footer = (t: ProfitRow): Record<string, ReactNode> => ({
    units: count(t.units),
    gross_sales: money(t.gross_sales),
    platform_fees: money(neg(t.platform_fees)),
    shipping_cost: money(neg(t.shipping_cost)),
    ad_spend: money(neg(t.ad_spend)),
    refund_amount: money(neg(t.refund_amount)),
    cogs: money(neg(t.cogs)),
    net_profit: <span className={tone(t.net_profit)}>{money(t.net_profit)}</span>,
    unit_profit: <span className={tone(t.unit_profit)}>{money(t.unit_profit)}</span>,
    margin: percent(t.margin),
    roi: percent(t.roi),
  })

  return (
    <Table>
      <TableHeader>
        {table.getHeaderGroups().map((hg) => (
          <TableRow key={hg.id}>
            {hg.headers.map((h) => {
              const sorted = h.column.getIsSorted()
              return (
                <TableHead
                  key={h.id}
                  className={`${isRight(h.column.columnDef.meta) ? 'text-right' : ''} ${
                    h.column.getCanSort() ? 'cursor-pointer select-none' : ''
                  }`}
                  onClick={h.column.getToggleSortingHandler()}
                >
                  <span className="inline-flex items-center gap-1">
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {sorted === 'asc' && <ArrowUp className="size-3" />}
                    {sorted === 'desc' && <ArrowDown className="size-3" />}
                  </span>
                </TableHead>
              )
            })}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={columns.length} className="text-muted-foreground py-10 text-center">
              {loading ? 'Cargando…' : 'Sin datos para este filtro. Importa órdenes en la pestaña Importar.'}
            </TableCell>
          </TableRow>
        )}
        {table.getRowModel().rows.map((row) => (
          <TableRow
            key={row.id}
            className={
              row.original.unit_profit != null && row.original.unit_profit < 0
                ? 'bg-red-50 dark:bg-red-950/30'
                : ''
            }
          >
            {row.getVisibleCells().map((cell) => (
              <TableCell key={cell.id} className={isRight(cell.column.columnDef.meta) ? 'text-right' : ''}>
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
      {totals && rows.length > 0 && (
        <TableFooter>
          <TableRow className="font-semibold">
            <TableCell>Total</TableCell>
            {table
              .getVisibleLeafColumns()
              .slice(1)
              .map((col) => (
                <TableCell key={col.id} className="text-right">
                  {footer(totals)[col.id] ?? ''}
                </TableCell>
              ))}
          </TableRow>
        </TableFooter>
      )}
    </Table>
  )
}
