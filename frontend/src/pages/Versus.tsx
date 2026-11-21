import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FilterBar } from '@/components/FilterBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, CHANNEL_LABEL, type Channel, type Filters, type GroupBy, type ProfitRow } from '@/lib/api'
import { count, money, percent, presetRange, previousRange, tone, type Preset } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type Mode = 'channel' | 'account' | 'product' | 'sku_channels' | 'period'

const MODES: { value: Mode; label: string }[] = [
  { value: 'channel', label: 'Canal vs canal' },
  { value: 'account', label: 'Tienda vs tienda' },
  { value: 'product', label: 'Producto vs producto' },
  { value: 'sku_channels', label: 'Mismo producto en dos canales' },
  { value: 'period', label: 'Periodo vs periodo anterior' },
]

interface Metric {
  key: keyof ProfitRow
  label: string
  fmt: (v: number | null) => string
  /** high = gana el mayor, low = gana el menor, null = no hay ganador (solo informativo). */
  better: 'high' | 'low' | null
}

const METRICS: Metric[] = [
  { key: 'gross_sales', label: 'Ventas', fmt: money, better: 'high' },
  { key: 'units', label: 'Unidades', fmt: count, better: 'high' },
  { key: 'promo', label: 'Promociones', fmt: money, better: null },
  { key: 'platform_fees', label: 'Comisiones', fmt: money, better: null },
  { key: 'shipping_cost', label: 'Envío', fmt: money, better: null },
  { key: 'ad_spend', label: 'Publicidad', fmt: money, better: null },
  { key: 'refund_amount', label: 'Reembolsos', fmt: money, better: 'low' },
  { key: 'cogs', label: 'Costo de productos', fmt: money, better: null },
  { key: 'net_profit', label: 'Utilidad neta', fmt: money, better: 'high' },
  { key: 'unit_profit', label: 'Utilidad por unidad', fmt: money, better: 'high' },
  { key: 'margin', label: 'Margen', fmt: percent, better: 'high' },
  { key: 'roi', label: 'ROI', fmt: percent, better: 'high' },
]

const A_COLOR = '#3b82f6'
const B_COLOR = '#f59e0b'

function num(row: ProfitRow | null | undefined, key: keyof ProfitRow): number | null {
  const v = row?.[key]
  return typeof v === 'number' ? v : null
}

/** Qué consulta pide cada modo (el periodo usa su propia consulta). */
const GROUP_BY: Record<Exclude<Mode, 'period'>, GroupBy> = {
  channel: 'channel',
  account: 'account',
  product: 'product',
  sku_channels: 'channel_product',
}

export function Versus() {
  const scope = useScope()
  const [mode, setMode] = useState<Mode>('channel')
  const [preset, setPreset] = useState<Preset>('90d')
  const [pickA, setPickA] = useState('')
  const [pickB, setPickB] = useState('')
  const [pickSku, setPickSku] = useState('')

  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: scope.channelParam, account: scope.account }),
    [preset, scope.channelParam, scope.account],
  )
  const prevRange = previousRange(preset)

  // Canal vs canal y mismo producto en dos canales miran todos los canales; tienda vs tienda respeta el canal elegido.
  const entityFilters = useMemo<Filters>(() => {
    if (mode === 'channel' || mode === 'sku_channels') return { ...filters, channel: '', account: '' }
    if (mode === 'account') return { ...filters, account: '' }
    return filters
  }, [mode, filters])

  const entities = useAsync(
    () => (mode === 'period' ? Promise.resolve(null) : api.profit(GROUP_BY[mode], entityFilters)),
    [mode, entityFilters],
  )
  const periodA = useAsync(
    () => (mode === 'period' ? api.profit('channel', filters) : Promise.resolve(null)),
    [mode, filters],
  )
  const periodB = useAsync(
    () =>
      mode === 'period' && prevRange
        ? api.profit('channel', { ...prevRange, channel: filters.channel, account: filters.account })
        : Promise.resolve(null),
    [mode, preset, filters.channel, filters.account],
  )

  const allRows = useMemo(() => entities.data?.rows ?? [], [entities.data])

  // Mismo producto en dos canales: se elige un SKU y las opciones son los canales donde se vendió.
  const skuOptions = useMemo(() => {
    const totals = new Map<string, { label: string; sales: number; channels: number }>()
    for (const r of allRows) {
      if (!r.sku) continue
      const t = totals.get(r.sku) ?? { label: r.product_name ?? r.sku, sales: 0, channels: 0 }
      t.sales += r.gross_sales
      t.channels += 1
      totals.set(r.sku, t)
    }
    return [...totals.entries()]
      .sort((x, y) => y[1].channels - x[1].channels || y[1].sales - x[1].sales)
      .map(([skuId, t]) => ({
        value: skuId,
        label: `${t.label} (${t.channels} ${t.channels === 1 ? 'canal' : 'canales'})`,
      }))
  }, [allRows])
  const sku = skuOptions.some((o) => o.value === pickSku) ? pickSku : (skuOptions[0]?.value ?? '')

  const rows = useMemo(
    () => (mode === 'sku_channels' ? allRows.filter((r) => r.sku === sku) : allRows),
    [mode, allRows, sku],
  )

  const idOf = (r: ProfitRow) => {
    if (mode === 'channel' || mode === 'sku_channels') return r.channel ?? ''
    if (mode === 'account') return `${r.channel}|${r.account}`
    return r.sku ?? ''
  }
  const labelOf = (r: ProfitRow) => {
    if (mode === 'channel' || mode === 'sku_channels') return CHANNEL_LABEL[r.channel as Channel] ?? r.channel ?? ''
    if (mode === 'account') return `${CHANNEL_LABEL[r.channel as Channel] ?? r.channel} · ${r.account}`
    return r.product_name ?? r.sku ?? ''
  }

  const sorted = useMemo(() => [...rows].sort((x, y) => y.gross_sales - x.gross_sales), [rows])
  const options = sorted.map((r) => ({ value: idOf(r), label: labelOf(r) }))
  // Si no eligieron (o cambió el modo), se comparan los dos de mayores ventas.
  const a = options.some((o) => o.value === pickA) ? pickA : (options[0]?.value ?? '')
  const b =
    options.some((o) => o.value === pickB) && pickB !== a
      ? pickB
      : (options.find((o) => o.value !== a)?.value ?? '')

  const rowA = mode === 'period' ? periodA.data?.totals : sorted.find((r) => idOf(r) === a)
  const rowB = mode === 'period' ? periodB.data?.totals : sorted.find((r) => idOf(r) === b)
  const nameA = mode === 'period' ? 'Periodo actual' : (options.find((o) => o.value === a)?.label ?? 'A')
  const nameB = mode === 'period' ? 'Periodo anterior' : (options.find((o) => o.value === b)?.label ?? 'B')

  const loading = entities.loading || periodA.loading || periodB.loading
  const error = entities.error ?? periodA.error ?? periodB.error
  const ready = rowA && rowB
  const chartData = [
    { name: 'Ventas', key: 'gross_sales' as const },
    { name: 'Utilidad neta', key: 'net_profit' as const },
  ].map((m) => ({ name: m.name, A: num(rowA, m.key) ?? 0, B: num(rowB, m.key) ?? 0 }))

  const ignoresScope = mode === 'channel' || mode === 'sku_channels'
  const scopeName = scope.account || (scope.channel === 'all' ? 'todos los canales' : CHANNEL_LABEL[scope.channel])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Comparativa head-to-head</h1>
        <FilterBar preset={preset} onPreset={setPreset} />
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
          <TabsList>
            {MODES.map((m) => (
              <TabsTrigger key={m.value} value={m.value}>
                {m.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        {mode === 'sku_channels' && skuOptions.length > 0 && (
          <Select items={skuOptions} value={sku} onValueChange={(v) => v && setPickSku(v)}>
            <SelectTrigger className="w-80" aria-label="Producto">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {skuOptions.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {mode !== 'period' && options.length > 0 && (
          <div className="flex items-center gap-2">
            <Select items={options} value={a} onValueChange={(v) => v && setPickA(v)}>
              <SelectTrigger className="w-56" aria-label="Opción A">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-muted-foreground text-sm">vs</span>
            <Select items={options} value={b} onValueChange={(v) => v && setPickB(v)}>
              <SelectTrigger className="w-56" aria-label="Opción B">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options
                  .filter((o) => o.value !== a)
                  .map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      {error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {error}
        </div>
      )}
      {mode === 'period' && !prevRange && (
        <p className="text-muted-foreground text-sm">
          Elige un periodo distinto de “Todo” para compararlo con el anterior de la misma duración.
        </p>
      )}
      {ignoresScope && (scope.channel !== 'all' || scope.account) && (
        <p className="text-muted-foreground text-sm">
          En esta vista se comparan todos los canales; el filtro de canal y tienda de la barra superior no aplica.
        </p>
      )}
      {(mode === 'product' || mode === 'account') && (
        <p className="text-muted-foreground text-sm">
          {mode === 'product'
            ? `Compara dos productos dentro de ${scopeName}.`
            : `Compara tiendas de ${scope.channel === 'all' ? 'todos los canales' : CHANNEL_LABEL[scope.channel]}.`}{' '}
          Cambia el canal o la tienda arriba para verlos en otro lugar.
        </p>
      )}
      {mode !== 'period' && !loading && options.length < 2 && (
        <p className="text-muted-foreground text-sm">
          {mode === 'sku_channels'
            ? 'Este producto solo se vendió en un canal en el periodo; elige otro producto.'
            : 'Hacen falta al menos dos opciones con ventas en el periodo para comparar.'}
        </p>
      )}

      {ready ? (
        <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
          <Card>
            <CardContent className="overflow-x-auto pt-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Métrica</TableHead>
                    <TableHead className="text-right" style={{ color: A_COLOR }}>
                      {nameA}
                    </TableHead>
                    <TableHead className="text-right" style={{ color: B_COLOR }}>
                      {nameB}
                    </TableHead>
                    <TableHead className="text-right">Diferencia (A − B)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {METRICS.map((m) => {
                    const va = num(rowA, m.key)
                    const vb = num(rowB, m.key)
                    const diff = va != null && vb != null ? va - vb : null
                    const aWins = m.better === 'high' ? (va ?? 0) > (vb ?? 0) : (va ?? 0) < (vb ?? 0)
                    const winner =
                      m.better && va != null && vb != null && va !== vb ? (aWins ? 'A' : 'B') : null
                    const win = 'font-semibold text-green-700'
                    return (
                      <TableRow key={m.key}>
                        <TableCell>{m.label}</TableCell>
                        <TableCell className={`text-right ${winner === 'A' ? win : ''}`}>{m.fmt(va)}</TableCell>
                        <TableCell className={`text-right ${winner === 'B' ? win : ''}`}>{m.fmt(vb)}</TableCell>
                        <TableCell className={`text-right ${tone(diff)}`}>
                          {diff == null ? '—' : m.fmt(diff)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
              <p className="text-muted-foreground mt-3 text-xs">
                En verde, el mejor valor de cada métrica con ganador claro (más ventas, utilidad y margen; menos
                reembolsos).
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Ventas y utilidad</CardTitle>
            </CardHeader>
            <CardContent className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" />
                  <YAxis tickFormatter={(v: number) => `$${v.toLocaleString('en-US')}`} width={72} />
                  <Tooltip formatter={(v) => money(Number(v))} />
                  <Legend />
                  <Bar dataKey="A" name={nameA} fill={A_COLOR} />
                  <Bar dataKey="B" name={nameB} fill={B_COLOR} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>
      ) : (
        !error && <p className="text-muted-foreground text-sm">{loading ? 'Cargando…' : 'Sin datos para comparar.'}</p>
      )}
    </div>
  )
}
