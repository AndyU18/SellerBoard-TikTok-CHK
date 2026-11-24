import { ArrowDown, ArrowUp, Columns3, Search, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { ErrorAlert } from '@/components/analytics/ui'
import { ChannelBadge } from '@/components/ChannelBadge'
import { useScopeDescription } from '@/components/PageHeader'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { ExpandableTable } from '@/components/charts/ExpandableTable'
import { ChartCard, EmptyChart, heatColor, type CsvRow } from '@/components/charts/kit'
import { Sparkline } from '@/components/charts/SparkKpi'
import { OrderLinesTable } from '@/components/dashboard/OrderLinesTable'
import { buildRows, grossProfit } from '@/components/dashboard/statementRows'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, CHANNEL_LABEL, type Filters, type GroupBy, type ProfitRow } from '@/lib/api'
import { chartdata } from '@/lib/chartdata'
import { count, money, neg, percent, tone } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const GROUPS: { id: GroupBy; label: string; noun: string }[] = [
  { id: 'product', label: 'Producto', noun: 'producto' },
  { id: 'channel', label: 'Canal', noun: 'canal' },
  { id: 'account', label: 'Tienda', noun: 'tienda' },
  {
    id: 'channel_product',
    label: 'Canal × producto',
    noun: 'canal y producto',
  },
]

type Col = {
  id: string
  header: string
  get: (r: ProfitRow) => number | null
  fmt: (v: number | null) => string
  /** Pinta el fondo con mapa de calor (verde alto, rojo bajo). */
  heat?: boolean
  color?: boolean
  bold?: boolean
  def: boolean
}

const COLS: Col[] = [
  {
    id: 'units',
    header: 'Unidades',
    get: (r) => r.units,
    fmt: count,
    def: true,
  },
  {
    id: 'refund_amount',
    header: 'Reembolsos',
    get: (r) => neg(r.refund_amount),
    fmt: money,
    def: true,
  },
  {
    id: 'gross_sales',
    header: 'Ventas',
    get: (r) => r.gross_sales,
    fmt: money,
    def: true,
  },
  {
    id: 'ad_spend',
    header: 'Publicidad',
    get: (r) => neg(r.ad_spend),
    fmt: money,
    def: true,
  },
  {
    id: 'gross_profit',
    header: 'Ganancia bruta',
    get: grossProfit,
    fmt: money,
    color: true,
    def: true,
  },
  {
    id: 'net_profit',
    header: 'Ganancia neta',
    get: (r) => r.net_profit,
    fmt: money,
    color: true,
    bold: true,
    def: true,
  },
  {
    id: 'margin',
    header: 'Margen',
    get: (r) => r.margin,
    fmt: percent,
    heat: true,
    def: true,
  },
  {
    id: 'roi',
    header: 'ROI',
    get: (r) => r.roi,
    fmt: percent,
    heat: true,
    def: true,
  },
  {
    id: 'promo',
    header: 'Promo',
    get: (r) => neg(r.promo),
    fmt: money,
    def: false,
  },
  {
    id: 'platform_fees',
    header: 'Comisiones',
    get: (r) => neg(r.platform_fees),
    fmt: money,
    def: false,
  },
  {
    id: 'shipping_cost',
    header: 'Envío',
    get: (r) => neg(r.shipping_cost),
    fmt: money,
    def: false,
  },
  {
    id: 'cogs',
    header: 'Costo de los bienes',
    get: (r) => neg(r.cogs),
    fmt: money,
    def: false,
  },
  {
    id: 'unit_profit',
    header: 'Ganancia / unid.',
    get: (r) => r.unit_profit,
    fmt: money,
    color: true,
    def: false,
  },
  {
    id: 'tacos',
    header: 'TACOS',
    get: (r) => r.tacos,
    fmt: percent,
    def: false,
  },
]
const TREND = 'trend'
const STORAGE = 'chk.dashboard.columns'
const ALL_IDS = [...COLS.map((c) => c.id), TREND]
const DEFAULT_IDS = [...COLS.filter((c) => c.def).map((c) => c.id), TREND]

function loadColumns(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE) ?? 'null')
    if (Array.isArray(raw)) {
      const ids = raw.filter((x): x is string => typeof x === 'string' && ALL_IDS.includes(x))
      if (ids.length) return ids
    }
  } catch {
    /* sin almacenamiento: columnas por defecto */
  }
  return DEFAULT_IDS
}

const rowKey = (r: ProfitRow) => [r.channel, r.account, r.sku].filter(Boolean).join('|')
const rowName = (r: ProfitRow) => r.product_name ?? r.sku ?? r.account ?? (r.channel ? CHANNEL_LABEL[r.channel] : '')
const CHIPS = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4']

/** Miniatura: no hay imágenes en el catálogo, así que se dibuja la inicial sobre un color estable. */
function Thumb({ name }: { name: string }) {
  const color = CHIPS[[...name].reduce((s, ch) => s + ch.charCodeAt(0), 0) % CHIPS.length]
  return (
    <span
      className="flex size-9 shrink-0 items-center justify-center rounded-md text-sm font-semibold text-white"
      style={{ background: color }}
      aria-hidden="true"
    >
      {name.trim().charAt(0).toUpperCase() || '?'}
    </span>
  )
}

function Identity({ r }: { r: ProfitRow }) {
  return (
    <div className="flex min-w-56 items-center gap-2">
      <Thumb name={rowName(r)} />
      <div className="min-w-0">
        <div className="truncate font-medium">{rowName(r)}</div>
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-2 text-xs">
          {r.channel && (r.sku || r.account) && <ChannelBadge channel={r.channel} />}
          {r.sku && <span>{r.sku}</span>}
          {r.account && !r.sku && <span>{r.account}</span>}
          {r.units > 0 && <span>{money(r.gross_sales / r.units)} / u.</span>}
        </div>
      </div>
    </div>
  )
}

const DETAIL: ComboSeries[] = [
  {
    key: 'net_profit',
    label: 'Ganancia neta',
    type: 'bar',
    color: '#10b981',
    format: money,
  },
  {
    key: 'ad_spend',
    label: 'Publicidad',
    type: 'bar',
    color: '#f59e0b',
    format: money,
  },
  {
    key: 'refund_amount',
    label: 'Reembolsos',
    type: 'bar',
    color: '#ef4444',
    format: money,
  },
  {
    key: 'gross_sales',
    label: 'Ventas',
    type: 'line',
    color: '#3b82f6',
    format: money,
  },
  {
    key: 'units',
    label: 'Unidades (eje derecho)',
    type: 'line',
    axis: 'right',
    color: '#8b5cf6',
    format: count,
  },
]
type DetailKey = 'net_profit' | 'ad_spend' | 'refund_amount' | 'gross_sales' | 'units'

function DetailPanel({ row, series, description, onClose }: { row: ProfitRow; series: ProfitRow[]; description: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const data = series.map((s) => ({
    period: String(s.period).slice(0, 7),
    net_profit: s.net_profit,
    ad_spend: s.ad_spend,
    refund_amount: s.refund_amount,
    gross_sales: s.gross_sales,
    units: s.units,
  }))
  const statement = buildRows(row, [])
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`Detalle de ${rowName(row)}`}>
      <button type="button" aria-label="Cerrar detalle" onClick={onClose} className="absolute inset-0 bg-black/40" />
      <aside className="relative h-full w-full max-w-xl space-y-4 overflow-y-auto bg-slate-50 p-4 shadow-xl dark:bg-slate-950">
        <div className="flex items-start justify-between gap-2">
          <Identity r={row} />
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded p-1.5 hover:bg-slate-200">
            <X className="size-5" />
          </button>
        </div>
        <ChartCard
          title={`Serie mensual de ${rowName(row)} — ${description}`}
          csv={data.map((d) => ({
            mes: d.period,
            ...Object.fromEntries(DETAIL.map((s) => [s.label, d[s.key as DetailKey]])),
          }))}
          filename={`detalle-${rowName(row)}`}
        >
          {data.length === 0 ? <EmptyChart /> : <ComboChart data={data} xKey="period" series={DETAIL} height={260} rightFormat={(v) => count(v)} />}
        </ChartCard>
        <ChartCard
          title={`Estado de resultados de ${rowName(row)}`}
          csv={statement.map((s) => ({
            concepto: s.label,
            monto: s.cells[0],
            porcentaje_ventas: s.cells[1],
          }))}
          filename={`estado-resultados-${rowName(row)}`}
        >
          <ExpandableTable columns={['Monto', '% ventas']} rows={statement} />
        </ChartCard>
      </aside>
    </div>
  )
}

function ColumnPicker({ visible, onChange }: { visible: string[]; onChange: (ids: string[]) => void }) {
  const toggle = (id: string) => onChange(visible.includes(id) ? visible.filter((x) => x !== id) : [...visible, id])
  return (
    <details className="relative">
      <summary className="flex cursor-pointer list-none items-center gap-1 rounded border px-2 py-1 text-xs hover:bg-slate-100">
        <Columns3 className="size-4" /> Columnas
      </summary>
      <div className="absolute right-0 z-20 mt-1 max-h-72 w-52 overflow-y-auto rounded-md border bg-white p-2 text-xs shadow-md dark:bg-slate-900">
        {[...COLS.map((c) => ({ id: c.id, header: c.header })), { id: TREND, header: 'Tendencia' }].map((c) => (
          <label key={c.id} className="flex cursor-pointer items-center gap-2 py-1">
            <input type="checkbox" checked={visible.includes(c.id)} onChange={() => toggle(c.id)} />
            {c.header}
          </label>
        ))}
        <button type="button" onClick={() => onChange(DEFAULT_IDS)} className="text-muted-foreground mt-1 underline">
          Restablecer
        </button>
      </div>
    </details>
  )
}

/** Tabla principal del Dashboard: pestañas Productos / Artículos del pedido, con agrupación, columnas y detalle lateral. */
export function ProductsExplorer({ filters }: { filters: Filters }) {
  const description = useScopeDescription()
  const [tab, setTab] = useState<'products' | 'lines'>('products')
  const [groupBy, setGroupBy] = useState<GroupBy>('product')
  const [visible, setVisible] = useState<string[]>(loadColumns)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<{ id: string; desc: boolean }>({
    id: 'net_profit',
    desc: true,
  })
  const [openKey, setOpenKey] = useState<string | null>(null)

  const setColumns = (ids: string[]) => {
    setVisible(ids)
    try {
      localStorage.setItem(STORAGE, JSON.stringify(ids))
    } catch {
      /* ignorado */
    }
  }

  const grouped = useAsync(() => api.profit(groupBy, filters), [groupBy, filters.start, filters.end, filters.channel, filters.account])
  const trend = useAsync(() => chartdata.versusSeries(groupBy, 'month', filters), [groupBy, filters.start, filters.end, filters.channel, filters.account])

  const series = useMemo(() => {
    const by = new Map<string, ProfitRow[]>()
    for (const r of trend.data?.rows ?? []) {
      const k = rowKey(r)
      by.set(k, [...(by.get(k) ?? []), r])
    }
    for (const list of by.values()) list.sort((a, b) => String(a.period).localeCompare(String(b.period)))
    return by
  }, [trend.data])

  const cols = COLS.filter((c) => visible.includes(c.id))
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    const list = (grouped.data?.rows ?? []).filter((r) => !q || [r.product_name, r.sku, r.account, r.channel].some((x) => x?.toLowerCase().includes(q)))
    const col = COLS.find((c) => c.id === sort.id)
    if (!col) return list
    return [...list].sort((a, b) => {
      const x = col.get(a)
      const y = col.get(b)
      if (x == null && y == null) return 0
      if (x == null) return 1
      if (y == null) return -1
      return sort.desc ? y - x : x - y
    })
  }, [grouped.data, search, sort])

  const heatRange = useMemo(() => {
    const out: Record<string, [number, number]> = {}
    for (const c of COLS.filter((c) => c.heat)) {
      const vals = rows.map(c.get).filter((v): v is number => v != null)
      out[c.id] = [Math.min(...vals), Math.max(...vals)]
    }
    return out
  }, [rows])

  const noun = GROUPS.find((g) => g.id === groupBy)?.noun ?? ''
  const totals = grouped.data?.totals ?? null
  const open = openKey ? (rows.find((r) => rowKey(r) === openKey) ?? null) : null
  const csv: CsvRow[] = rows.map((r) => ({
    nombre: rowName(r),
    canal: r.channel ? CHANNEL_LABEL[r.channel] : '',
    tienda: r.account,
    sku: r.sku,
    ...Object.fromEntries(cols.map((c) => [c.header, c.get(r)])),
  }))

  const tabs = (
    <div className="mr-2 flex gap-1" role="group" aria-label="Vista de la tabla">
      {(
        [
          ['products', 'Productos'],
          ['lines', 'Artículos del pedido'],
        ] as const
      ).map(([id, label]) => (
        <button
          key={id}
          type="button"
          aria-pressed={tab === id}
          onClick={() => setTab(id)}
          className={`rounded border px-2 py-1 text-xs ${tab === id ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`}
        >
          {label}
        </button>
      ))}
    </div>
  )

  if (tab === 'lines') return <OrderLinesTable filters={filters} tabs={tabs} />

  const header = (c: { id: string; header: string; sortable: boolean }): ReactNode => (
    <TableHead
      key={c.id}
      className={`${c.id === 'name' ? '' : 'text-right'} ${c.sortable ? 'cursor-pointer select-none' : ''}`}
      aria-sort={sort.id === c.id ? (sort.desc ? 'descending' : 'ascending') : undefined}
      onClick={c.sortable ? () => setSort({ id: c.id, desc: sort.id === c.id ? !sort.desc : true }) : undefined}
    >
      <span className="inline-flex items-center gap-1">
        {c.header}
        {sort.id === c.id && (sort.desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
      </span>
    </TableHead>
  )

  return (
    <>
      <ChartCard
        title={`Ventas por ${noun} — ${description}`}
        subtitle="Toca una fila para ver su serie mensual y su estado de resultados. Margen y ROI van de rojo (más bajo) a verde (más alto)."
        csv={csv}
        filename={`dashboard-por-${groupBy}`}
        actions={tabs}
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="relative min-w-44 flex-1 sm:max-w-xs">
            <Search className="text-muted-foreground absolute top-2 left-2 size-4" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={`Buscar ${noun}…`}
              aria-label={`Buscar ${noun}`}
              className="border-input h-8 w-full rounded-lg border bg-transparent pr-2 pl-8 text-sm outline-none focus-visible:ring-2"
            />
          </div>
          <label className="flex items-center gap-1 text-xs">
            Agrupar por
            <select
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value as GroupBy)}
              className="border-input h-8 rounded-lg border bg-transparent px-2 text-sm"
            >
              {GROUPS.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>
          <div className="ml-auto">
            <ColumnPicker visible={visible} onChange={setColumns} />
          </div>
        </div>
        <ErrorAlert message={grouped.error ?? trend.error} />
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                {header({
                  id: 'name',
                  header: GROUPS.find((g) => g.id === groupBy)?.label ?? '',
                  sortable: false,
                })}
                {cols.map((c) => header({ id: c.id, header: c.header, sortable: true }))}
                {visible.includes(TREND) && header({ id: TREND, header: 'Tendencia', sortable: false })}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={cols.length + 2} className="text-muted-foreground py-10 text-center">
                    {grouped.loading ? 'Cargando…' : search ? 'Ningún resultado coincide con la búsqueda.' : 'Sin datos para este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => {
                const k = rowKey(r)
                const spark = (series.get(k) ?? []).map((s) => s.net_profit)
                return (
                  <TableRow
                    key={k}
                    tabIndex={0}
                    onClick={() => setOpenKey(k)}
                    onKeyDown={(e) => e.key === 'Enter' && setOpenKey(k)}
                    className="cursor-pointer hover:bg-slate-50"
                  >
                    <TableCell>
                      <Identity r={r} />
                    </TableCell>
                    {cols.map((c) => {
                      const v = c.get(r)
                      const [lo, hi] = heatRange[c.id] ?? [0, 0]
                      const style =
                        c.heat && v != null
                          ? {
                              background: heatColor(hi > lo ? (v - lo) / (hi - lo) : 0.5),
                              color: '#0f172a',
                            }
                          : undefined
                      return (
                        <TableCell key={c.id} className={`text-right ${c.bold ? 'font-semibold' : ''} ${c.color ? tone(v) : ''}`} style={style}>
                          {c.fmt(v)}
                        </TableCell>
                      )
                    })}
                    {visible.includes(TREND) && (
                      <TableCell className="text-right">
                        <Sparkline values={spark} color={(spark.at(-1) ?? 0) >= 0 ? '#10b981' : '#ef4444'} />
                      </TableCell>
                    )}
                  </TableRow>
                )
              })}
            </TableBody>
            {totals && rows.length > 0 && !search && (
              <tfoot>
                <TableRow className="font-semibold">
                  <TableCell>Total</TableCell>
                  {cols.map((c) => (
                    <TableCell key={c.id} className={`text-right ${c.color ? tone(c.get(totals)) : ''}`}>
                      {c.fmt(c.get(totals))}
                    </TableCell>
                  ))}
                  {visible.includes(TREND) && <TableCell />}
                </TableRow>
              </tfoot>
            )}
          </Table>
        </div>
      </ChartCard>
      {open && <DetailPanel row={open} series={series.get(openKey ?? '') ?? []} description={description} onClose={() => setOpenKey(null)} />}
    </>
  )
}
