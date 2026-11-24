import { Download } from 'lucide-react'
import { useMemo, useState } from 'react'
import { CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, NumberField, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { ChartCard, COLOR, EmptyChart, TipBox, axisMoney, type CsvRow } from '@/components/charts/kit'
import { UsTileMap } from '@/components/charts/UsTileMap'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CHANNEL_LABEL, type Channel } from '@/lib/api'
import { count, money } from '@/lib/format'
import { MAX_TARGET_MARGIN, minPrice, type MinPriceRow, type MinPriceState, type MinPriceStatus, type ShippingBasis } from '@/lib/minPrice'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const DEFAULT_MARGIN_PCT = 0
const MAX_BAR_ROWS = 15 // barras de brecha: las de peor brecha
const BELOW_COLOR = COLOR.bad

const STATUS: Record<MinPriceStatus, { label: string; pill: string }> = {
  below: { label: 'Bajo el mínimo', pill: PILL.red },
  ok: { label: 'Sobre el mínimo', pill: PILL.green },
  unreachable: { label: 'Sin piso posible', pill: PILL.amber },
  no_cost: { label: 'Sin costo', pill: PILL.slate },
  insufficient: { label: 'Pocas ventas', pill: PILL.slate },
}

const BASIS_LABEL: Record<ShippingBasis, string> = { real: 'real', estimated: 'estimado', none: 'sin dato' }

type MapMetric = 'min_price' | 'shipping_unit'
const MAP_METRICS: { value: MapMetric; label: string }[] = [
  { value: 'min_price', label: 'Precio mínimo' },
  { value: 'shipping_unit', label: 'Costo de envío' },
]

type SortKey = 'sku' | 'channel' | 'units' | 'realized_price' | 'shipping_unit' | 'min_price' | 'gap' | 'below_pct' | 'estimated_loss'
type Sort = { key: SortKey; dir: 1 | -1 }

const COLUMNS: { key: SortKey; label: string; align?: 'right' }[] = [
  { key: 'sku', label: 'SKU' },
  { key: 'channel', label: 'Canal' },
  { key: 'units', label: 'Unid.', align: 'right' },
  { key: 'realized_price', label: 'Precio realizado', align: 'right' },
  { key: 'shipping_unit', label: 'Envío / u.', align: 'right' },
  { key: 'min_price', label: 'Precio mínimo', align: 'right' },
  { key: 'gap', label: 'Brecha', align: 'right' },
  { key: 'below_pct', label: '% bajo piso', align: 'right' },
  { key: 'estimated_loss', label: 'Pérdida est.', align: 'right' },
]

const rowKey = (r: MinPriceRow) => `${r.channel}|${r.sku}`
const channelName = (key: string) => CHANNEL_LABEL[key as Channel] ?? key
const isJudged = (r: MinPriceRow) => r.status === 'below' || r.status === 'ok'

function sortValue(r: MinPriceRow, key: SortKey): string | number | null {
  return key === 'channel' ? channelName(r.channel) : r[key]
}

/** Ordena por columna; los vacíos (null) siempre al final, sea cual sea la dirección. */
function sortRows(rows: MinPriceRow[], { key, dir }: Sort): MinPriceRow[] {
  return [...rows].sort((a, b) => {
    const x = sortValue(a, key)
    const y = sortValue(b, key)
    if (x == null || y == null) return x == null && y == null ? 0 : x == null ? 1 : -1
    return (typeof x === 'string' ? x.localeCompare(y as string) : x - (y as number)) * dir
  })
}

function matches(r: MinPriceRow, q: string): boolean {
  const needle = q.trim().toLowerCase()
  return !needle || r.sku.toLowerCase().includes(needle) || (r.product_name ?? '').toLowerCase().includes(needle)
}

function downloadCsv(filename: string, rows: CsvRow[]) {
  if (!rows.length) return
  const cols = Object.keys(rows[0])
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const text = [cols.map(esc).join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
  const url = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function csvRow(r: MinPriceRow): CsvRow {
  return {
    sku: r.sku,
    canal: channelName(r.channel),
    unidades: r.units,
    precio_realizado: r.realized_price.toFixed(2),
    cogs_unitario: r.cogs_unit.toFixed(2),
    envio_unitario: r.shipping_unit.toFixed(2),
    base_envio: BASIS_LABEL[r.shipping_basis],
    comision_pct: (r.fee_rate * 100).toFixed(1),
    reembolso_unitario: r.refund_unit.toFixed(2),
    precio_minimo: r.min_price?.toFixed(2),
    brecha: r.gap?.toFixed(2),
    pct_bajo_piso: r.below_pct == null ? '' : (r.below_pct * 100).toFixed(1),
    perdida_estimada: r.estimated_loss.toFixed(2),
    estados_con_perdida: r.loss_states.join('; '),
    estado: STATUS[r.status].label,
  }
}

type FloorPoint = { id: string; label: string; x: number; y: number; z: number; below: boolean; gap: number | null }

/**
 * Precio realizado (x) contra precio mínimo (y). La diagonal es el equilibrio: sobre ella el piso es mayor
 * que lo que se cobra, o sea se pierde. Rojo = SKU con ventas bajo su piso; verde = sobre su piso.
 */
function FloorBubble({ points, onPointClick, height = 340 }: { points: FloorPoint[]; onPointClick: (id: string) => void; height?: number }) {
  if (!points.length) return <EmptyChart>Ningún SKU con costo y ventas suficientes para calcular un piso.</EmptyChart>
  const top = Math.max(1, ...points.map((p) => Math.max(p.x, p.y)))
  const bySide = [true, false].map((below) => points.filter((p) => p.below === below))
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-full" style={{ background: BELOW_COLOR }} />
          Bajo el mínimo
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-full" style={{ background: COLOR.good }} />
          Sobre el mínimo
        </span>
        <span className="text-muted-foreground flex items-center gap-1.5">
          <span className="inline-block h-0 w-5 border-t-2 border-dashed border-slate-400" />
          Equilibrio (cobrado = mínimo)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="x" name="Precio realizado" tickFormatter={axisMoney} tick={{ fontSize: 11 }} domain={[0, 'auto']} label={{ value: 'Precio realizado', position: 'insideBottom', offset: -4, fontSize: 11 }} />
          <YAxis type="number" dataKey="y" name="Precio mínimo" tickFormatter={axisMoney} tick={{ fontSize: 11 }} width={60} domain={[0, 'auto']} label={{ value: 'Precio mínimo', angle: -90, position: 'insideLeft', fontSize: 11 }} />
          <ZAxis type="number" dataKey="z" range={[60, 500]} name="Unidades" />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: top, y: top }]} stroke="#94a3b8" strokeDasharray="5 5" ifOverflow="extendDomain" />
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as FloorPoint | undefined) : undefined
              if (!p) return null
              return (
                <TipBox
                  title={p.label}
                  rows={[
                    { label: 'Precio realizado', value: money(p.x) },
                    { label: 'Precio mínimo', value: money(p.y) },
                    { label: 'Brecha', value: money(p.gap) },
                    { label: 'Unidades', value: count(p.z) },
                  ]}
                />
              )
            }}
          />
          {bySide.map((side, i) => (
            <Scatter
              key={i === 0 ? 'below' : 'ok'}
              data={side}
              cursor="pointer"
              onClick={(d: unknown) => {
                const p = (d as { payload?: FloorPoint }).payload
                if (p) onPointClick(p.id)
              }}
            >
              {side.map((p) => (
                <Cell key={p.id} fill={p.below ? BELOW_COLOR : COLOR.good} fillOpacity={0.65} />
              ))}
            </Scatter>
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

/** Desglose de la fórmula del piso: de dónde sale el precio mínimo del SKU elegido. */
function FloorBreakdown({ row, margin }: { row: MinPriceRow; margin: number }) {
  const lines: [string, string][] = [
    ['Costo del producto (COGS)', money(row.cogs_unit)],
    [`Envío por unidad (${BASIS_LABEL[row.shipping_basis]})`, money(row.shipping_unit)],
    ['Reembolsos por unidad', money(row.refund_unit)],
    ['Comisión de la plataforma', pct1(row.fee_rate)],
    ['Margen objetivo', pct1(margin)],
  ]
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
      {lines.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-b py-1">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="font-medium tabular-nums">{v}</dd>
        </div>
      ))}
      <div className="flex justify-between gap-3 py-1 sm:col-span-2">
        <dt className="font-medium">Precio mínimo = (COGS + envío + reembolso) / (1 − comisión − margen)</dt>
        <dd className="font-semibold tabular-nums">{row.min_price == null ? '—' : money(row.min_price)}</dd>
      </div>
    </dl>
  )
}

function StateTable({ states, selected, onSelect }: { states: MinPriceState[]; selected: string | null; onSelect: (s: string) => void }) {
  return (
    <div className="max-h-72 overflow-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Estado</TableHead>
            <TableHead className="text-right">Unid.</TableHead>
            <TableHead className="text-right">Envío / u.</TableHead>
            <TableHead className="text-right">Mínimo</TableHead>
            <TableHead className="text-right">Cobrado</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {states.map((s) => (
            <TableRow key={s.state} onClick={() => onSelect(s.state)} className={`cursor-pointer ${selected === s.state ? 'bg-blue-50' : ''}`}>
              <TableCell>
                {s.state}
                {s.loses_money && <span className="ml-2 text-xs text-red-700">pierde</span>}
              </TableCell>
              <TableCell className="text-right">{count(s.units)}</TableCell>
              <TableCell className="text-right" title={s.shipping_basis === 'sku' ? 'Pocas etiquetas: promedio del SKU' : 'Promedio real del estado'}>
                {money(s.shipping_unit)}
                {s.shipping_basis === 'sku' && '*'}
              </TableCell>
              <TableCell className="text-right">{money(s.min_price)}</TableCell>
              <TableCell className={`text-right ${s.loses_money ? 'font-medium text-red-700' : ''}`}>{money(s.realized_price)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function SkuDetail({ row, margin }: { row: MinPriceRow; margin: number }) {
  const [metric, setMetric] = useState<MapMetric>('min_price')
  const [stateName, setStateName] = useState<string | null>(null)
  const mapData = useMemo(
    () =>
      row.states.flatMap((s) => {
        const value = metric === 'min_price' ? s.min_price : s.shipping_unit
        return value == null ? [] : [{ state: s.state, value, extra: `${s.units} u. · cobrado ${money(s.realized_price)}` }]
      }),
    [row, metric],
  )
  const byName = new Map(row.states.map((s) => [s.state, s]))
  const picked = stateName ? byName.get(stateName) : undefined
  const csv = row.states.map<CsvRow>((s) => ({
    estado: s.state,
    unidades: s.units,
    envio_unitario: s.shipping_unit.toFixed(2),
    precio_minimo: s.min_price?.toFixed(2),
    precio_realizado: s.realized_price.toFixed(2),
    pierde_dinero: s.loses_money ? 'sí' : 'no',
  }))

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">
              {row.sku} <span className="text-muted-foreground text-sm font-normal">· {channelName(row.channel)}</span>
            </CardTitle>
            {row.product_name && <p className="text-muted-foreground mt-1 text-xs">{row.product_name}</p>}
          </div>
          <Pill label={STATUS[row.status].label} className={STATUS[row.status].pill} />
        </CardHeader>
        <CardContent className="space-y-3">
          <FloorBreakdown row={row} margin={margin} />
          {row.loss_states.length > 0 && (
            <Callout tone="danger">
              Pierde dinero en {row.loss_states.length} {row.loss_states.length === 1 ? 'estado' : 'estados'}: {row.loss_states.join(', ')}.
            </Callout>
          )}
          {row.shipping_basis !== 'real' && (
            <Callout tone="info">Este SKU no tiene etiquetas de Redo en el período: el envío es {BASIS_LABEL[row.shipping_basis]}, el piso puede quedar corto.</Callout>
          )}
        </CardContent>
      </Card>

      {row.states.length === 0 ? (
        <Card>
          <CardContent className="py-6">
            <EmptyChart>Este SKU no tiene ventas con estado de destino en el período.</EmptyChart>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          <ChartCard
            title="Por estado de destino"
            subtitle="Los estados lejanos suben el piso: la etiqueta cuesta más."
            csv={csv}
            filename={`precio-minimo-${row.sku}-estados`}
            actions={<Segmented label="Métrica del mapa" value={metric} options={MAP_METRICS} onChange={setMetric} />}
          >
            <UsTileMap
              data={mapData}
              format={money}
              selected={stateName}
              onStateClick={setStateName}
              describe={(name, value, extra) => `${name}: ${value == null ? 'sin ventas' : money(value)}${extra ? ` · ${extra}` : ''}`}
            />
            {picked && (
              <p className="mt-2 text-sm">
                <strong>{picked.state}</strong>: mínimo {money(picked.min_price)}, cobrado {money(picked.realized_price)}, envío {money(picked.shipping_unit)} por unidad
                {picked.loses_money ? ' — aquí se pierde dinero.' : '.'}
              </p>
            )}
          </ChartCard>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Estados del SKU</CardTitle>
              <p className="text-muted-foreground text-xs">* Pocas etiquetas en el estado: se usó el envío promedio del SKU.</p>
            </CardHeader>
            <CardContent>
              <StateTable states={row.states} selected={stateName} onSelect={setStateName} />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}

function SkuTable({ rows, sort, onSort, selectedKey, onSelect }: { rows: MinPriceRow[]; sort: Sort; onSort: (k: SortKey) => void; selectedKey: string | null; onSelect: (k: string) => void }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {COLUMNS.map((c) => (
            <TableHead key={c.key} className={c.align === 'right' ? 'text-right' : ''} aria-sort={sort.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
              <button type="button" onClick={() => onSort(c.key)} className="hover:underline">
                {c.label}
                {sort.key === c.key && (sort.dir === 1 ? ' ▲' : ' ▼')}
              </button>
            </TableHead>
          ))}
          <TableHead>Estado</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={COLUMNS.length + 1} className="text-muted-foreground py-8 text-center">
              Ningún SKU coincide con la búsqueda.
            </TableCell>
          </TableRow>
        )}
        {rows.map((r) => (
          <TableRow key={rowKey(r)} onClick={() => onSelect(rowKey(r))} className={`cursor-pointer ${selectedKey === rowKey(r) ? 'bg-blue-50' : ''}`}>
            <TableCell className="font-mono text-xs">{r.sku}</TableCell>
            <TableCell>{channelName(r.channel)}</TableCell>
            <TableCell className="text-right">{count(r.units)}</TableCell>
            <TableCell className="text-right">{money(r.realized_price)}</TableCell>
            <TableCell className="text-right" title={`Envío ${BASIS_LABEL[r.shipping_basis]}`}>
              {money(r.shipping_unit)}
              {r.shipping_basis !== 'real' && <span className="text-muted-foreground"> ({BASIS_LABEL[r.shipping_basis]})</span>}
            </TableCell>
            <TableCell className="text-right font-medium">{money(r.min_price)}</TableCell>
            <TableCell className={`text-right ${r.gap != null && r.gap < 0 ? 'text-red-700' : ''}`}>{money(r.gap)}</TableCell>
            <TableCell className="text-right">{pct1(r.below_pct)}</TableCell>
            <TableCell className={`text-right ${r.estimated_loss > 0 ? 'font-medium text-red-700' : ''}`}>{money(r.estimated_loss)}</TableCell>
            <TableCell>
              <Pill label={STATUS[r.status].label} className={STATUS[r.status].pill} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Precio mínimo con el costo REAL de las etiquetas de Redo por estado: qué SKU se venden bajo su piso. */
export function MinPrice({ onNavigate }: { onNavigate?: (page: string) => void }) {
  const { preset, channelParam, account } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const [marginPct, setMarginPct] = useState(DEFAULT_MARGIN_PCT)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<Sort>({ key: 'estimated_loss', dir: -1 })
  const [picked, setPicked] = useState<string | null>(null)

  const margin = marginPct / 100
  const data = useAsync(() => minPrice.report({ days, targetMargin: margin, channel: channelParam, account }), [days, margin, channelParam, account])
  const report = data.data
  const allRows = useMemo(() => report?.rows ?? [], [report])
  const multiChannel = new Set(allRows.map((r) => r.channel)).size > 1

  const tableRows = useMemo(() => sortRows(allRows.filter((r) => matches(r, search)), sort), [allRows, search, sort])
  const active = allRows.find((r) => rowKey(r) === picked) ?? allRows.find((r) => r.status === 'below') ?? allRows.find(isJudged) ?? null
  const activeKey = active ? rowKey(active) : null

  const points = useMemo<FloorPoint[]>(
    () =>
      allRows
        .filter((r) => isJudged(r) && r.min_price != null)
        .map((r) => ({
          id: rowKey(r),
          label: multiChannel ? `${r.sku} · ${channelName(r.channel)}` : r.sku,
          x: r.realized_price,
          y: r.min_price as number,
          z: r.units,
          below: r.status === 'below',
          gap: r.gap,
        })),
    [allRows, multiChannel],
  )
  const bars = useMemo<HBarRow[]>(
    () =>
      allRows
        .filter((r) => isJudged(r) && r.gap != null)
        .sort((a, b) => (a.gap as number) - (b.gap as number))
        .slice(0, MAX_BAR_ROWS)
        .map((r) => ({ id: rowKey(r), label: multiChannel ? `${r.sku} · ${channelName(r.channel)}` : r.sku, gap: r.gap })),
    [allRows, multiChannel],
  )

  const onSort = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: key === 'sku' || key === 'channel' ? 1 : -1 }))
  const summary = report?.summary
  const lowCoverage = summary?.label_coverage != null && summary.label_coverage < 0.5

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Precio mínimo con envío real</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
        <Hint>
          El piso es el precio al que un SKU no pierde dinero: (costo + envío + reembolsos) / (1 − comisión − margen objetivo). El envío sale del costo real de las etiquetas de Redo por estado de destino, no de un promedio.
        </Hint>
      </header>

      <ErrorAlert message={data.error} />

      <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-3">
        <Kpi
          title="SKU bajo el mínimo"
          value={summary ? `${summary.below_skus} de ${summary.skus}` : data.loading ? 'Cargando…' : '—'}
          hint={summary ? `${summary.skus_real_shipping} con envío real · ${summary.skus_estimated_shipping} con envío estimado` : undefined}
          className={summary && summary.below_skus > 0 ? 'text-red-600' : ''}
        />
        <Kpi
          title="Pérdida estimada"
          value={summary ? money(summary.estimated_loss) : data.loading ? 'Cargando…' : '—'}
          hint={`Faltante de cobro de las ventas bajo su piso en ${days} días`}
          className={summary && summary.estimated_loss > 0 ? 'text-red-600' : ''}
        />
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Margen objetivo</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <div className="text-2xl font-semibold">{marginPct}%</div>
            <NumberField label="Cambiar (0 = equilibrio)" value={marginPct} onCommit={setMarginPct} min={0} max={Math.floor(MAX_TARGET_MARGIN * 100)} step={1} suffix="%" />
          </CardContent>
        </Card>
      </section>

      {lowCoverage && summary?.label_coverage != null && (
        <Callout tone="warning">
          Solo {pct1(summary.label_coverage)} de las unidades tiene etiqueta de Redo: en las demás el envío se estima y el piso puede quedar corto.
        </Callout>
      )}

      {data.loading && !report && <EmptyChart>Cargando…</EmptyChart>}
      {report && allRows.length === 0 && <EmptyChart>No hay ventas en este período y alcance.</EmptyChart>}

      {report && allRows.length > 0 && (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Precio realizado vs precio mínimo"
              subtitle="Cada burbuja es un SKU y canal; el tamaño son las unidades. Sobre la diagonal el piso supera lo que se cobra."
              csv={allRows.filter(isJudged).map(csvRow)}
              filename="precio-minimo-dispersion"
            >
              <FloorBubble points={points} onPointClick={setPicked} />
            </ChartCard>
            <ChartCard
              title="Brecha contra el piso por SKU"
              subtitle={`Precio realizado menos precio mínimo; negativo = bajo el piso. Los ${MAX_BAR_ROWS} peores. Clic para ver el detalle.`}
              csv={bars.map<CsvRow>((b) => ({ sku: b.label, brecha: Number(b.gap).toFixed(2) }))}
              filename="precio-minimo-brecha"
            >
              <HBarChart
                rows={bars}
                series={[{ key: 'gap', label: 'Brecha vs. piso' }]}
                format={money}
                refLine={0}
                selectedId={activeKey}
                rowColor={(r) => (Number(r.gap) < 0 ? BELOW_COLOR : COLOR.good)}
                onRowClick={(r) => setPicked(r.id)}
              />
            </ChartCard>
          </div>

          {active && <SkuDetail key={activeKey} row={active} margin={margin} />}

          <Card>
            <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
              <CardTitle className="text-base">Todos los SKU</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <Input type="search" placeholder="Buscar SKU o producto…" aria-label="Buscar SKU o producto" className="h-9 w-56" value={search} onChange={(e) => setSearch(e.target.value)} />
                <Button type="button" variant="outline" size="sm" onClick={() => downloadCsv('precio-minimo-sku.csv', tableRows.map(csvRow))} disabled={!tableRows.length}>
                  <Download className="mr-1 size-4" /> CSV
                </Button>
              </div>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <SkuTable rows={tableRows} sort={sort} onSort={onSort} selectedKey={activeKey} onSelect={setPicked} />
            </CardContent>
          </Card>
        </>
      )}

      {onNavigate && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => onNavigate('pricing')}>
            Ir a Precios
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => onNavigate('simulator')}>
            Probar en el Simulador
          </Button>
        </div>
      )}
    </div>
  )
}
