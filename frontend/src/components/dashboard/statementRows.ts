import type { TreeRow } from '@/components/charts/ExpandableTable'
import { CHANNEL_LABEL, type ProfitRow } from '@/lib/api'
import { count, money, neg, percent } from '@/lib/format'

export const grossProfit = (t: ProfitRow) => t.gross_sales - t.promo - t.refund_amount - t.platform_fees - t.shipping_cost - t.cogs

/** Filas del estado de resultados (un monto y su % de las ventas) para un total y su desglose por canal. */
export function buildRows(t: ProfitRow, channels: ProfitRow[]): TreeRow[] {
  const sales = t.gross_sales
  const pct = (v: number) => (sales > 0 ? percent(v / sales) : '—')
  const line = (id: string, label: string, value: number, opts: { cost?: boolean; bold?: boolean } = {}): TreeRow => {
    const shown = opts.cost ? neg(value) : value
    return {
      id,
      label,
      cells: [money(shown), pct(shown)],
      negative: [shown < 0, false],
      bold: opts.bold,
    }
  }
  const ratio = (id: string, label: string, v: number | null): TreeRow => ({
    id,
    label,
    cells: [percent(v), ''],
    shaded: true,
  })
  return [
    {
      ...line('sales', 'Ventas', t.gross_sales, { bold: true }),
      children: channels
        .filter((c) => c.channel)
        .map((c) => ({
          id: `sales-${c.channel}`,
          label: CHANNEL_LABEL[c.channel!],
          cells: [money(c.gross_sales), pct(c.gross_sales)],
        })),
    },
    { id: 'units', label: 'Unidades', cells: [count(t.units), ''] },
    line('refunds', 'Reembolsos', t.refund_amount, { cost: true }),
    line('promo', 'Promo', t.promo, { cost: true }),
    line('ads', 'Publicidad', t.ad_spend, { cost: true }),
    line('shipping', 'Envío', t.shipping_cost, { cost: true }),
    line('fees', 'Tarifas de plataforma', t.platform_fees, { cost: true }),
    line('cogs', 'Costo de los bienes', t.cogs, { cost: true }),
    line('gross', 'Ganancia bruta', grossProfit(t), { bold: true }),
    line('indirect', 'Gastos indirectos', t.allocated_expenses, { cost: true }),
    line('net', 'Ganancia neta', t.net_profit, { bold: true }),
    ratio('tacos', 'TACOS', t.tacos),
    ratio('refund-rate', 'Tasa de reembolso', sales > 0 ? t.refund_amount / sales : null),
    ratio('margin', 'Margen', t.margin),
    ratio('roi', 'ROI', t.roi),
  ]
}
