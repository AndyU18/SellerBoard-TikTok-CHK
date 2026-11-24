import { ArrowDown, ArrowUp, Columns3, Download, Package, RefreshCw, Save, Search, X } from 'lucide-react'
import { Fragment, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { ChartCard, COLOR, EmptyChart, heatColor, TipBox } from '@/components/charts/kit'
import { Sparkline } from '@/components/charts/SparkKpi'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { api, CHANNEL_COLOR, CHANNEL_LABEL, type Channel, type InventoryResponse, type StockStatus } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { chartdataProductos } from '@/lib/chartdata_productos'
import { count, money, percent } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type CogsFilter = 'any' | 'with' | 'without'
type GroupKey = 'none' | 'channel' | 'stock'
type ColId = 'channels' | 'cogs' | 'unit' | 'velocity' | 'stock' | 'sales' | 'profit' | 'margin' | 'trend'

interface Row {
  sku: string
  name: string
  id: number | null
  cogs: number
  hasCost: boolean
  channels: Channel[]
  price: number | null
  units: number
  sales: number
  profit: number
  unitProfit: number | null
  margin: number | null
  perDay: number
  stock: number | null
  stockStatus: StockStatus | null
  trend: number[]
}

const COLUMNS: { id: ColId; label: string }[] = [
  { id: 'channels', label: 'Canales' },
  { id: 'cogs', label: 'COGS' },
  { id: 'unit', label: 'Beneficio / unid.' },
  { id: 'velocity', label: 'Velocidad (u/día)' },
  { id: 'stock', label: 'Inventario' },
  { id: 'sales', label: 'Ventas' },
  { id: 'profit', label: 'Beneficio' },
  { id: 'margin', label: 'Margen' },
  { id: 'trend', label: 'Tendencia 12 sem.' },
]
const STORAGE_KEY = 'chk.products.columns'
const PAGE_SIZE = 25

const STOCK_LABEL: Record<StockStatus, string> = {
  out_of_stock: 'Agotado',
  low: 'Por agotarse',
  no_stock: 'Sin existencias',
  unpublished: 'Despublicado',
  no_sales: 'Sin ventas',
  ok: 'En orden',
}
const STOCK_RANK: StockStatus[] = ['out_of_stock', 'low', 'no_stock', 'unpublished', 'no_sales', 'ok']
const STOCK_TONE: Record<StockStatus, string> = {
  out_of_stock: 'bg-red-100 text-red-800',
  low: 'bg-amber-100 text-amber-800',
  no_stock: 'bg-red-50 text-red-700',
  unpublished: 'bg-slate-100 text-slate-700',
  no_sales: 'bg-slate-100 text-slate-700',
  ok: 'bg-green-100 text-green-800',
}

const selectClass =
  'border-input bg-background h-8 rounded-lg border px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50'

function loadColumns(): ColId[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const v = JSON.parse(raw) as unknown
      if (Array.isArray(v)) return COLUMNS.map((c) => c.id).filter((id) => v.includes(id))
    }
  } catch {
    /* sin almacenamiento: columnas por defecto */
  }
  return COLUMNS.map((c) => c.id)
}

function worstStatus(a: StockStatus | null, b: StockStatus): StockStatus {
  return a == null || STOCK_RANK.indexOf(b) < STOCK_RANK.indexOf(a) ? b : a
}

function download(rows: Row[]) {
  const head = ['Producto', 'SKU', 'Canales', 'Precio', 'COGS', 'Beneficio por unidad', 'Unidades por día', 'Inventario', 'Estado inventario', 'Ventas', 'Beneficio', 'Margen']
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = rows.map((r) =>
    [
      r.name,
      r.sku,
      r.channels.map((c) => CHANNEL_LABEL[c]).join(' / '),
      r.price?.toFixed(2),
      r.hasCost ? r.cogs.toFixed(2) : '',
      r.unitProfit?.toFixed(2),
      r.perDay.toFixed(2),
      r.stock,
      r.stockStatus ? STOCK_LABEL[r.stockStatus] : '',
      r.sales.toFixed(2),
      r.profit.toFixed(2),
      r.margin == null ? '' : (r.margin * 100).toFixed(1) + '%',
    ]
      .map(esc)
      .join(','),
  )
  const blob = new Blob(['﻿' + [head.map(esc).join(','), ...lines].join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'productos.csv'
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function Products() {
  const scope = useScope()
  const { can } = useAuth()
  const description = useScopeDescription()
  const editable = can('costs.edit') || can('catalog.edit')
  const [version, setVersion] = useState(0)

  // Filtros: el borrador se edita en la barra; "Filtrar" lo aplica.
  const [draft, setDraft] = useState<{ q: string; cogs: CogsFilter; channel: Channel | ''; group: GroupKey }>({
    q: '',
    cogs: 'any',
    channel: '',
    group: 'none',
  })
  const [applied, setApplied] = useState(draft)
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 }>({ col: 'sales', dir: -1 })
  const [page, setPage] = useState(0)
  const [cols, setCols] = useState<ColId[]>(loadColumns)
  const [showCols, setShowCols] = useState(false)
  const [pending, setPending] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)

  const range = scope.range
  const overview = useAsync(
    () => chartdataProductos.productsOverview({ ...range, channel: scope.channelParam, account: scope.account }),
    [range.start, range.end, scope.channelParam, scope.account, version],
  )
  const products = useAsync(() => api.products(), [version])
  const inventory = useAsync(
    (): Promise<InventoryResponse | null> =>
      api.inventory({ channel: scope.channelParam, account: scope.account }).catch(() => null),
    [scope.channelParam, scope.account, version],
  )

  const rows = useMemo<Row[]>(() => {
    const map = new Map<string, Row>()
    const blank = (sku: string, name: string | null): Row => ({
      sku,
      name: name ?? sku,
      id: null,
      cogs: 0,
      hasCost: false,
      channels: [],
      price: null,
      units: 0,
      sales: 0,
      profit: 0,
      unitProfit: null,
      margin: null,
      perDay: 0,
      stock: null,
      stockStatus: null,
      trend: [],
    })
    for (const o of overview.data?.rows ?? []) {
      const r = blank(o.sku, o.product_name)
      Object.assign(r, {
        channels: [...o.channels],
        price: o.avg_price,
        units: o.units,
        sales: o.gross_sales,
        profit: o.net_profit,
        unitProfit: o.unit_profit,
        margin: o.margin,
        perDay: o.units_per_day,
        trend: o.weekly_profit,
      })
      map.set(o.sku, r)
    }
    for (const p of products.data ?? []) {
      const r = map.get(p.sku) ?? blank(p.sku, p.name)
      r.id = p.id
      r.cogs = p.cogs_unit
      r.hasCost = p.has_cost
      if (p.name) r.name = p.name
      map.set(p.sku, r)
    }
    for (const i of inventory.data?.rows ?? []) {
      const r = map.get(i.sku)
      if (!r) continue
      r.stock = (r.stock ?? 0) + (i.avail_to_sell ?? 0)
      r.stockStatus = worstStatus(r.stockStatus, i.status)
      if (i.price != null && r.price == null) r.price = i.price
      const ch = i.channel as Channel
      if (!r.channels.includes(ch)) r.channels.push(ch)
    }
    return [...map.values()]
  }, [overview.data, products.data, inventory.data])

  const filtered = useMemo(() => {
    const q = applied.q.trim().toLowerCase()
    return rows.filter(
      (r) =>
        (!q || `${r.sku} ${r.name}`.toLowerCase().includes(q)) &&
        (applied.cogs === 'any' || (applied.cogs === 'with') === r.hasCost) &&
        (!applied.channel || r.channels.includes(applied.channel)),
    )
  }, [rows, applied])

  const value = (r: Row, col: string): number | string | null => {
    switch (col) {
      case 'product':
        return r.name.toLowerCase()
      case 'channels':
        return r.channels.length
      case 'cogs':
        return r.hasCost ? r.cogs : null
      case 'unit':
        return r.unitProfit
      case 'velocity':
        return r.perDay
      case 'stock':
        return r.stock
      case 'sales':
        return r.sales
      case 'profit':
        return r.profit
      case 'margin':
        return r.margin
      default:
        return r.trend.reduce((s, v) => s + v, 0)
    }
  }

  const groupsOf = (r: Row): string[] => {
    if (applied.group === 'channel') return r.channels.length ? r.channels.map((c) => CHANNEL_LABEL[c]) : ['Sin ventas ni listados']
    if (applied.group === 'stock') return [r.stockStatus ? STOCK_LABEL[r.stockStatus] : 'Sin dato de inventario']
    return ['']
  }

  const ordered = useMemo(() => {
    const cmp = (a: Row, b: Row) => {
      const x = value(a, sort.col)
      const y = value(b, sort.col)
      if (x == null && y == null) return a.sku.localeCompare(b.sku)
      if (x == null) return 1
      if (y == null) return -1
      const d = typeof x === 'string' ? x.localeCompare(String(y)) : x - (y as number)
      return d * sort.dir || a.sku.localeCompare(b.sku)
    }
    const groups = new Map<string, Row[]>()
    for (const r of filtered) for (const g of groupsOf(r)) groups.set(g, [...(groups.get(g) ?? []), r])
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .flatMap(([g, list]) => [...list].sort(cmp).map((r) => ({ g, r })))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, sort, applied.group])

  const groupTotals = useMemo(() => {
    const t = new Map<string, { n: number; sales: number; profit: number }>()
    for (const { g, r } of ordered) {
      const c = t.get(g) ?? { n: 0, sales: 0, profit: 0 }
      t.set(g, { n: c.n + 1, sales: c.sales + r.sales, profit: c.profit + r.profit })
    }
    return t
  }, [ordered])

  const pages = Math.max(1, Math.ceil(ordered.length / PAGE_SIZE))
  const current = Math.min(page, pages - 1)
  const visible = ordered.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE)

  const top10 = useMemo(
    () =>
      [...rows]
        .filter((r) => r.units > 0)
        .sort((a, b) => b.profit - a.profit)
        .slice(0, 10)
        .map((r) => ({ sku: r.sku, label: r.name.length > 28 ? `${r.name.slice(0, 27)}…` : r.name, name: r.name, profit: r.profit })),
    [rows],
  )
  const bubbles = useMemo<BubblePoint[]>(
    () =>
      rows
        .filter((r) => r.sales > 0 && r.margin != null)
        .map((r) => ({
          id: r.sku,
          label: `${r.name} (${r.sku})`,
          x: r.sales,
          y: r.margin as number,
          z: Math.max(r.units, 1),
          group: r.channels.length === 1 ? CHANNEL_LABEL[r.channels[0]] : r.channels.length > 1 ? 'Varios canales' : 'Sin canal',
        })),
    [rows],
  )

  // COGS pendiente de guardar: solo lo que es un número válido y distinto del actual.
  const dirty = useMemo(() => {
    const out: { id: number; sku: string; value: number }[] = []
    for (const r of rows) {
      if (r.id == null) continue
      const raw = pending[r.id]
      if (raw === undefined || raw === '') continue
      const v = Number(raw)
      if (!Number.isNaN(v) && v >= 0 && (v !== r.cogs || !r.hasCost)) out.push({ id: r.id, sku: r.sku, value: v })
    }
    return out
  }, [rows, pending])

  const saveAll = async () => {
    setSaving(true)
    setNotice(null)
    const results = await Promise.allSettled(dirty.map((d) => api.updateCogs(d.id, d.value)))
    const failed = results.flatMap((res, i) => (res.status === 'rejected' ? [dirty[i].sku] : []))
    const okIds = dirty.filter((_, i) => results[i].status === 'fulfilled').map((d) => d.id)
    setPending((p) => Object.fromEntries(Object.entries(p).filter(([k]) => !okIds.includes(Number(k)))))
    setSaving(false)
    setNotice(
      failed.length
        ? { kind: 'error', text: `No se pudo guardar el COGS de: ${failed.join(', ')}.` }
        : { kind: 'ok', text: `${okIds.length} costo(s) guardado(s).` },
    )
    if (okIds.length) setVersion((v) => v + 1)
  }

  const apply = () => {
    setApplied(draft)
    setPage(0)
  }
  const pickProduct = (sku: string) => {
    const next = { ...draft, q: sku }
    setDraft(next)
    setApplied(next)
    setPage(0)
  }
  const toggleCol = (id: ColId) => {
    const next = cols.includes(id) ? cols.filter((c) => c !== id) : COLUMNS.map((c) => c.id).filter((c) => c === id || cols.includes(c))
    setCols(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* ignorado */
    }
  }
  const sortBy = (col: string) => setSort((s) => (s.col === col ? { col, dir: (s.dir * -1) as 1 | -1 } : { col, dir: col === 'product' ? 1 : -1 }))
  const reload = () => setVersion((v) => v + 1)

  const loading = overview.loading || products.loading
  const error = overview.error ?? products.error
  const show = (id: ColId) => cols.includes(id)
  const colSpan = 1 + cols.length

  const Head = ({ id, label, right = false }: { id: string; label: string; right?: boolean }) => (
    <th className={`text-muted-foreground h-10 px-3 text-xs font-medium whitespace-nowrap ${right ? 'text-right' : 'text-left'}`}>
      <button type="button" onClick={() => sortBy(id)} className="hover:text-foreground inline-flex items-center gap-1">
        {label}
        {sort.col === id && (sort.dir === 1 ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </th>
  )

  return (
    <div className="space-y-6 pb-20">
      <h1 className="text-2xl font-semibold">Productos — {description}</h1>

      {error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {error}
        </div>
      )}
      {notice && (
        <div
          role={notice.kind === 'error' ? 'alert' : 'status'}
          className={`rounded-md border p-3 text-sm ${notice.kind === 'error' ? 'border-red-300 bg-red-50 text-red-800' : 'border-green-300 bg-green-50 text-green-900'}`}
        >
          {notice.text}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Top 10 productos por beneficio"
          subtitle="Beneficio neto del período; clic en una barra para filtrar la tabla"
          csv={top10.map((t) => ({ sku: t.sku, producto: t.name, beneficio: t.profit }))}
          filename="top-productos-beneficio"
        >
          {top10.length === 0 ? (
            <EmptyChart>{loading ? 'Cargando…' : 'Sin ventas en este período.'}</EmptyChart>
          ) : (
            <ResponsiveContainer width="100%" height={340}>
              <BarChart data={top10} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tickFormatter={(v: number) => money(v)} tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="label" width={150} tick={{ fontSize: 11 }} />
                <Tooltip
                  cursor={{ fill: 'rgba(148,163,184,0.15)' }}
                  content={({ active, payload }) => {
                    const p = active ? (payload?.[0]?.payload as (typeof top10)[number] | undefined) : undefined
                    return p ? <TipBox title={p.name} rows={[{ label: 'Beneficio', value: money(p.profit) }]} /> : null
                  }}
                />
                <Bar dataKey="profit" cursor="pointer" onClick={(d: unknown) => pickProduct((d as { sku: string }).sku)}>
                  {top10.map((t) => (
                    <Cell key={t.sku} fill={t.profit >= 0 ? COLOR.good : COLOR.bad} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>
        <ChartCard
          title="Ventas vs. margen"
          subtitle="Cada burbuja es un producto (tamaño = unidades); clic para filtrar la tabla"
          csv={bubbles.map((b) => ({ sku: b.id, producto: b.label, ventas: b.x, margen: b.y, unidades: b.z }))}
          filename="ventas-vs-margen"
        >
          <BubbleChart
            points={bubbles}
            xLabel="Ventas"
            yLabel="Margen"
            zLabel="Unidades"
            xFormat={(v) => money(v)}
            yFormat={(v) => percent(v)}
            zFormat={(v) => count(v)}
            yRef={0}
            onPointClick={(p) => pickProduct(p.id)}
          />
        </ChartCard>
      </div>

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          apply()
        }}
      >
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Buscar</span>
          <div className="relative">
            <Search className="text-muted-foreground absolute top-2 left-2 size-4" />
            <Input
              placeholder="SKU o nombre"
              aria-label="Buscar producto"
              className="h-8 w-56 pl-8"
              value={draft.q}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            />
          </div>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">COGS</span>
          <select className={selectClass} value={draft.cogs} onChange={(e) => setDraft({ ...draft, cogs: e.target.value as CogsFilter })}>
            <option value="any">Cualquiera</option>
            <option value="with">Con costo</option>
            <option value="without">Sin costo</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Se vende en</span>
          <select className={selectClass} value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value as Channel | '' })}>
            <option value="">Todos los canales</option>
            {(Object.keys(CHANNEL_LABEL) as Channel[]).map((c) => (
              <option key={c} value={c}>
                {CHANNEL_LABEL[c]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Agrupar por</span>
          <select className={selectClass} value={draft.group} onChange={(e) => setDraft({ ...draft, group: e.target.value as GroupKey })}>
            <option value="none">Sin agrupar</option>
            <option value="channel">Canal</option>
            <option value="stock">Estado del inventario</option>
          </select>
        </label>
        <Button type="submit" size="sm" className="h-8">
          Filtrar
        </Button>
        {applied.q && (
          <button
            type="button"
            className="bg-secondary inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs"
            onClick={() => {
              const next = { ...draft, q: '' }
              setDraft(next)
              setApplied(next)
            }}
          >
            «{applied.q}» <X className="size-3" />
          </button>
        )}
        <div className="relative ml-auto">
          <Button type="button" variant="outline" size="sm" className="h-8" onClick={() => setShowCols((s) => !s)} aria-expanded={showCols}>
            <Columns3 /> Columnas
          </Button>
          {showCols && (
            <div className="bg-popover absolute right-0 z-20 mt-1 w-52 rounded-lg border p-2 shadow-md">
              {COLUMNS.map((c) => (
                <label key={c.id} className="flex items-center gap-2 px-1 py-1 text-sm">
                  <input type="checkbox" checked={cols.includes(c.id)} onChange={() => toggleCol(c.id)} />
                  {c.label}
                </label>
              ))}
            </div>
          )}
        </div>
      </form>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <table className="w-full caption-bottom text-sm">
            <thead className="border-b">
              <tr>
                <Head id="product" label="Producto" />
                {show('channels') && <Head id="channels" label="Canales" />}
                {show('cogs') && <Head id="cogs" label="COGS" right />}
                {show('unit') && <Head id="unit" label="Beneficio / unid." right />}
                {show('velocity') && <Head id="velocity" label="Velocidad (u/día)" right />}
                {show('stock') && <Head id="stock" label="Inventario" right />}
                {show('sales') && <Head id="sales" label="Ventas" right />}
                {show('profit') && <Head id="profit" label="Beneficio" right />}
                {show('margin') && <Head id="margin" label="Margen" right />}
                {show('trend') && <Head id="trend" label="Tendencia 12 sem." />}
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 && (
                <tr>
                  <td colSpan={colSpan} className="text-muted-foreground py-10 text-center">
                    {loading ? 'Cargando…' : 'No hay productos con este filtro.'}
                  </td>
                </tr>
              )}
              {visible.map(({ g, r }, i) => {
                const t = groupTotals.get(g)
                const newGroup = applied.group !== 'none' && (i === 0 || visible[i - 1].g !== g)
                const heat = r.margin == null ? undefined : heatColor(Math.max(0, Math.min(1, (r.margin + 0.1) / 0.5)))
                const raw = r.id == null ? undefined : pending[r.id]
                return (
                  <Fragment key={`${g}|${r.sku}`}>
                    {newGroup && t && (
                      <tr className="bg-muted/50">
                        <td colSpan={colSpan} className="px-3 py-1.5 text-xs font-semibold">
                          {g} <span className="text-muted-foreground font-normal">— {t.n} productos · ventas {money(t.sales)} · beneficio {money(t.profit)}</span>
                        </td>
                      </tr>
                    )}
                    <tr className="hover:bg-muted/30 border-b">
                      <td className="px-3 py-2">
                        <div className="flex min-w-56 items-center gap-3">
                          <span className="bg-muted text-muted-foreground flex size-9 shrink-0 items-center justify-center rounded-md">
                            <Package className="size-4" />
                          </span>
                          <div className="flex flex-col">
                            <span className="font-medium">{r.name}</span>
                            <span className="text-muted-foreground text-xs">
                              {r.sku}
                              {r.price != null && ` · ${money(r.price)}`}
                            </span>
                          </div>
                        </div>
                      </td>
                      {show('channels') && (
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap gap-1">
                            {r.channels.length === 0 && <span className="text-muted-foreground text-xs">—</span>}
                            {r.channels.map((c) => (
                              <span key={c} className="rounded px-1.5 py-0.5 text-[11px] font-medium text-white" style={{ background: CHANNEL_COLOR[c] }}>
                                {CHANNEL_LABEL[c]}
                              </span>
                            ))}
                          </div>
                        </td>
                      )}
                      {show('cogs') && (
                        <td className="px-3 py-2 text-right">
                          {editable && r.id != null ? (
                            <Input
                              type="number"
                              min={0}
                              step="0.01"
                              placeholder="0.00"
                              aria-label={`COGS de ${r.sku}`}
                              className={`ml-auto h-8 w-24 text-right ${raw !== undefined && raw !== '' ? 'border-amber-400' : ''} ${!r.hasCost && raw === undefined ? 'bg-amber-50' : ''}`}
                              value={raw ?? (r.hasCost ? String(r.cogs) : '')}
                              onChange={(e) => setPending((p) => ({ ...p, [r.id as number]: e.target.value }))}
                            />
                          ) : r.hasCost ? (
                            money(r.cogs)
                          ) : (
                            <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">Sin costo</span>
                          )}
                        </td>
                      )}
                      {show('unit') && (
                        <td className={`px-3 py-2 text-right tabular-nums ${(r.unitProfit ?? 0) < 0 ? 'text-red-600' : ''}`}>{money(r.unitProfit)}</td>
                      )}
                      {show('velocity') && <td className="px-3 py-2 text-right tabular-nums">{r.perDay.toFixed(2)}</td>}
                      {show('stock') && (
                        <td className="px-3 py-2 text-right">
                          {r.stock == null ? (
                            <span className="text-muted-foreground text-xs">—</span>
                          ) : (
                            <div className="flex flex-col items-end gap-0.5">
                              <span className="tabular-nums">{count(r.stock)}</span>
                              {r.stockStatus && <span className={`rounded px-1.5 text-[11px] ${STOCK_TONE[r.stockStatus]}`}>{STOCK_LABEL[r.stockStatus]}</span>}
                            </div>
                          )}
                        </td>
                      )}
                      {show('sales') && <td className="px-3 py-2 text-right tabular-nums">{money(r.sales)}</td>}
                      {show('profit') && (
                        <td className={`px-3 py-2 text-right tabular-nums ${r.profit < 0 ? 'text-red-600' : ''}`}>{money(r.profit)}</td>
                      )}
                      {show('margin') && (
                        <td className="px-3 py-2 text-right tabular-nums" style={{ background: heat }}>
                          {percent(r.margin)}
                        </td>
                      )}
                      {show('trend') && (
                        <td className="px-3 py-2">
                          <Sparkline values={r.trend} color={r.trend.reduce((s, v) => s + v, 0) >= 0 ? COLOR.blue : COLOR.bad} />
                        </td>
                      )}
                    </tr>
                  </Fragment>
                )
              })}
            </tbody>
          </table>
          <div className="text-muted-foreground mt-3 flex flex-wrap items-center justify-between gap-2 text-xs">
            <span>
              {count(ordered.length)} filas · velocidad de ventas de los últimos {overview.data?.velocity_days ?? 30} días
            </span>
            <span className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={current === 0} onClick={() => setPage(current - 1)}>
                Anterior
              </Button>
              Página {current + 1} de {pages}
              <Button variant="outline" size="sm" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>
                Siguiente
              </Button>
            </span>
          </div>
        </CardContent>
      </Card>

      <div className="bg-background/95 fixed inset-x-0 bottom-0 z-30 flex flex-wrap items-center justify-end gap-2 border-t px-4 py-2 backdrop-blur">
        <span className="text-muted-foreground mr-auto text-xs">
          {dirty.length > 0 ? `${dirty.length} cambio(s) de COGS sin guardar` : 'Sin cambios pendientes'}
        </span>
        <Button variant="outline" size="sm" onClick={() => download(ordered.map((o) => o.r))} disabled={ordered.length === 0}>
          <Download /> Exportar CSV
        </Button>
        <Button variant="outline" size="sm" onClick={reload} disabled={loading}>
          <RefreshCw /> Recargar
        </Button>
        {editable && (
          <Button size="sm" onClick={() => void saveAll()} disabled={dirty.length === 0 || saving}>
            <Save /> {saving ? 'Guardando…' : `Guardar${dirty.length ? ` (${dirty.length})` : ''}`}
          </Button>
        )}
      </div>
    </div>
  )
}
