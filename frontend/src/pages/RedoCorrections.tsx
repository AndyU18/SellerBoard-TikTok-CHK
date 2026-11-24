import { Download } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, pct1 } from '@/components/analytics/ui'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { Donut } from '@/components/charts/Donut'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset, redo, type RedoSkuCorrection } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type SortKey = 'sku' | 'shipped_orders' | 'orders_with_correction' | 'correction_rate' | 'correction_amount' | 'avg_correction' | 'correction_pct_of_labels'

const TOP_BARS = 10
const NORMAL = 'Sin alerta'
const FLAGGED = 'Revisar medidas'

function downloadCsv(rows: RedoSkuCorrection[]) {
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const head = ['SKU', 'Producto', 'Ordenes enviadas', 'Ordenes con correccion', 'Tasa de correccion %', 'Monto de correcciones', 'Correccion promedio', 'Costo de etiquetas', '% sobre etiquetas', 'Motivos principales', 'Revisar medidas']
  const body = rows.map((r) => [
    r.sku,
    r.name,
    r.shipped_orders,
    r.orders_with_correction,
    Math.round(r.correction_rate * 10000) / 100,
    r.correction_amount,
    r.avg_correction == null ? '' : Math.round(r.avg_correction * 100) / 100,
    r.label_cost,
    r.correction_pct_of_labels == null ? '' : Math.round(r.correction_pct_of_labels * 10000) / 100,
    r.top_reasons.map((t) => `${t.reason} (${t.count})`).join(' | '),
    r.flag ? 'sí' : 'no',
  ])
  const text = [head, ...body].map((line) => line.map(esc).join(',')).join('\n')
  const url = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = 'redo-correcciones-por-sku.csv'
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function RedoCorrections() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const data = useAsync(() => redo.carrierCorrections(days), [days])
  const d = data.data
  const s = d?.summary
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'correction_amount', dir: -1 })
  const [search, setSearch] = useState('')
  const [onlyFlagged, setOnlyFlagged] = useState(false)
  const empty = (msg: string) => <EmptyChart>{data.loading ? 'Cargando…' : msg}</EmptyChart>

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const list = (d?.skus ?? []).filter(
      (r) => (!onlyFlagged || r.flag) && (!needle || r.sku.toLowerCase().includes(needle) || (r.name ?? '').toLowerCase().includes(needle)),
    )
    list.sort((a, b) => {
      const av = a[sort.key] ?? -Infinity
      const bv = b[sort.key] ?? -Infinity
      if (typeof av === 'string' && typeof bv === 'string') return av.localeCompare(bv) * sort.dir
      return ((av as number) - (bv as number)) * sort.dir
    })
    return list
  }, [d?.skus, search, onlyFlagged, sort])

  const topBars = useMemo(() => (d?.skus ?? []).filter((r) => r.correction_amount > 0).slice(0, TOP_BARS), [d?.skus])
  const maxBar = Math.max(1, ...topBars.map((r) => r.correction_amount))

  const bubbles = useMemo<BubblePoint[]>(
    () =>
      (d?.skus ?? []).map((r) => ({
        id: r.sku,
        label: `${r.sku}${r.name ? ` · ${r.name}` : ''}`,
        x: r.shipped_orders,
        y: r.correction_rate,
        z: Math.max(r.correction_amount, 1),
        group: r.flag ? FLAGGED : NORMAL,
      })),
    [d?.skus],
  )
  const reasonSlices = (s?.reasons ?? []).map((r) => ({ id: r.reason, label: `${r.reason} (${count(r.count)})`, value: r.amount }))

  const toggle = (key: SortKey) =>
    setSort((cur) => (cur.key === key ? { key, dir: cur.dir === 1 ? -1 : 1 } : { key, dir: key === 'sku' ? 1 : -1 }))
  const head = (key: SortKey, label: string, right = true) => (
    <TableHead className={right ? 'text-right' : ''}>
      <button type="button" onClick={() => toggle(key)} className="hover:underline">
        {label}
        {sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
      </button>
    </TableHead>
  )

  const thresholdPct = d ? Math.round(d.thresholds.min_rate * 100) : 15
  const thresholdOrders = d?.thresholds.min_orders ?? 5

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Correcciones del carrier por SKU{scope ? ` · ${scope}` : ''}</h1>
        {d && (
          <p className="text-muted-foreground text-xs">
            Del {d.window.start} al {d.window.end} (período global: {days} días)
          </p>
        )}
      </div>

      <ErrorAlert message={data.error} />
      {data.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Correcciones totales"
          value={money(s?.total_corrections ?? 0)}
          hint={`${count(s?.correction_count ?? 0)} cargos de corrección`}
          className={(s?.total_corrections ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="SKUs para revisar"
          value={count(s?.flagged_skus ?? 0)}
          hint={`${thresholdOrders}+ órdenes y tasa de corrección de ${thresholdPct}% o más`}
          className={(s?.flagged_skus ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi title="Monto en SKUs marcados" value={money(s?.flagged_amount ?? 0)} hint="correcciones atribuidas a esos productos" />
        <Kpi title="% sobre etiquetas" value={pct1(s?.pct_of_labels)} hint={`sobre ${money(s?.label_cost ?? 0)} en etiquetas`} />
      </div>

      {(s?.unattributed.count ?? 0) > 0 && (
        <Callout tone="info">
          {money(s?.unattributed.amount ?? 0)} en {count(s?.unattributed.count)} correcciones no se pueden atribuir a un SKU: la orden no
          está en las ventas de la app (o no tiene etiqueta en este período).
        </Callout>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={`SKUs con más correcciones ($) · ${scope}`}
          subtitle={`Los ${TOP_BARS} productos con mayor monto de correcciones. En rojo, los marcados para revisar medidas.`}
          filename="redo-correcciones-top-sku"
          csv={topBars.map((r) => ({ SKU: r.sku, Producto: r.name, Correcciones: r.correction_amount, 'Tasa %': Math.round(r.correction_rate * 10000) / 100 }))}
        >
          {topBars.length === 0 ? (
            empty('Sin correcciones atribuidas a SKUs en este período.')
          ) : (
            <ul className="space-y-2">
              {topBars.map((r) => (
                <li key={r.sku} className="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-2 text-xs sm:grid-cols-[minmax(0,10rem)_1fr_auto]">
                  <span className="truncate font-mono" title={`${r.sku}${r.name ? ` · ${r.name}` : ''}`}>
                    {r.sku}
                  </span>
                  <span className="h-4 rounded bg-slate-100">
                    <span
                      className="block h-4 rounded"
                      style={{ width: `${(r.correction_amount / maxBar) * 100}%`, background: r.flag ? COLOR.bad : COLOR.blue }}
                    />
                  </span>
                  <span className="w-16 text-right tabular-nums">{money(r.correction_amount)}</span>
                </li>
              ))}
            </ul>
          )}
        </ChartCard>

        <ChartCard
          title="Motivos de las correcciones"
          subtitle="Monto por motivo que reporta el carrier (nota del cargo)."
          filename="redo-correcciones-motivos"
          csv={(s?.reasons ?? []).map((r) => ({ Motivo: r.reason, Veces: r.count, Monto: r.amount }))}
        >
          {reasonSlices.length === 0 ? empty('Sin correcciones en este período.') : <Donut slices={reasonSlices} format={money} centerLabel="en correcciones" />}
        </ChartCard>
      </div>

      <ChartCard
        title="Órdenes enviadas frente a tasa de corrección"
        subtitle={`Cada burbuja es un SKU; tamaño = monto de correcciones. La línea marca el umbral de ${thresholdPct}%: arriba de ella y con ${thresholdOrders}+ órdenes se marca el SKU.`}
        filename="redo-correcciones-dispersion"
        csv={bubbles.map((b) => ({ SKU: b.id, 'Ordenes enviadas': b.x, 'Tasa de correccion': b.y, 'Monto': b.z }))}
      >
        {bubbles.length === 0 ? (
          empty('Sin SKUs con etiquetas en este período.')
        ) : (
          <BubbleChart
            points={bubbles}
            xLabel="Órdenes enviadas"
            yLabel="Tasa de corrección"
            zLabel="Monto de correcciones"
            xFormat={count}
            yFormat={(v) => `${Math.round(v * 100)}%`}
            zFormat={money}
            yRef={d?.thresholds.min_rate}
            onPointClick={(p) => setSearch(p.id)}
          />
        )}
      </ChartCard>

      <Card>
        <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>Detalle por SKU</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar SKU o nombre"
              aria-label="Buscar SKU o nombre"
              className="h-9 w-full rounded-md border px-3 text-sm sm:w-56"
            />
            <label className="flex items-center gap-1.5 text-xs">
              <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} />
              Solo marcados
            </label>
            <button
              type="button"
              onClick={() => downloadCsv(rows)}
              disabled={rows.length === 0}
              className="text-muted-foreground hover:text-foreground flex items-center gap-1 rounded border px-2 py-1.5 text-xs hover:bg-slate-100 disabled:opacity-40"
            >
              <Download className="size-4" /> CSV
            </button>
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                {head('sku', 'SKU', false)}
                {head('shipped_orders', 'Órdenes')}
                {head('orders_with_correction', 'Con corrección')}
                {head('correction_rate', 'Tasa')}
                {head('correction_amount', 'Monto')}
                {head('avg_correction', 'Promedio')}
                {head('correction_pct_of_labels', '% etiquetas')}
                <TableHead>Motivos principales</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="text-muted-foreground py-8 text-center">
                    {data.loading ? 'Cargando…' : 'Ningún SKU coincide o no hay órdenes con etiqueta en este período.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={r.sku} className={r.flag ? 'bg-red-50' : ''}>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs">{r.sku}</span>
                      {r.flag && <Pill label="Revisar medidas" className={PILL.red} />}
                    </div>
                    {r.name && <div className="text-muted-foreground max-w-xs truncate text-xs">{r.name}</div>}
                    {r.flag && r.suggestion && <div className="max-w-xs text-xs whitespace-normal text-red-700">{r.suggestion}</div>}
                  </TableCell>
                  <TableCell className="text-right">{count(r.shipped_orders)}</TableCell>
                  <TableCell className="text-right">{count(r.orders_with_correction)}</TableCell>
                  <TableCell className={`text-right ${r.flag ? 'font-medium text-red-700' : ''}`}>{pct1(r.correction_rate)}</TableCell>
                  <TableCell className="text-right font-medium">{money(r.correction_amount)}</TableCell>
                  <TableCell className="text-right">{money(r.avg_correction)}</TableCell>
                  <TableCell className="text-right">{pct1(r.correction_pct_of_labels)}</TableCell>
                  <TableCell className="text-muted-foreground max-w-xs text-xs break-words whitespace-normal">
                    {r.top_reasons.length === 0 ? '—' : r.top_reasons.map((t) => `${t.reason} (${count(t.count)})`).join(' · ')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Hint>
        Una corrección es un recargo que el carrier cobra sobre la etiqueta cuando el peso o las medidas reales no coinciden con
        lo declarado. Si una orden lleva varios SKU, el monto se reparte según la venta bruta de cada uno. Se marca un SKU con{' '}
        {thresholdOrders}+ órdenes enviadas y {thresholdPct}% o más de ellas con corrección.
      </Hint>
    </div>
  )
}
