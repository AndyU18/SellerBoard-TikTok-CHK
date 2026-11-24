import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { Donut } from '@/components/charts/Donut'
import { ChartCard, EmptyChart, PALETTE } from '@/components/charts/kit'
import { Waterfall, type WaterfallStep } from '@/components/charts/Waterfall'
import { useScopeDescription } from '@/components/PageHeader'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { settlements, type ConceptKind, type ConceptRow } from '@/lib/settlements'
import { useAsync } from '@/lib/useAsync'

const KIND_LABEL: Record<ConceptKind, { label: string; pill: string }> = {
  revenue: { label: 'Venta', pill: PILL.blue },
  fee: { label: 'Comisión', pill: PILL.amber },
  shipping: { label: 'Envío', pill: PILL.slate },
  adjustment: { label: 'Ajuste', pill: PILL.slate },
  memo: { label: 'Informativo', pill: PILL.slate },
}

const pctText = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`)
const shortName = (name: string) => name.replace(/ \(.*\)/, '')
const monthLabel = (v: string | number) => {
  const [y, m] = String(v).split('-')
  const names = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
  return `${names[Number(m) - 1] ?? m} ${y}`
}

/** Variación del costo contra el período anterior: rojo si el costo crece, verde si baja. */
function Change({ row }: { row: ConceptRow }) {
  if (row.kind === 'memo') return <span className="text-muted-foreground">—</span>
  if (row.change_pct == null) {
    return row.cost > 0 && row.previous_cost === 0 ? (
      <span className="font-medium text-red-600">nuevo</span>
    ) : (
      <span className="text-muted-foreground">—</span>
    )
  }
  const up = row.change_pct > 0
  return (
    <span className={`font-medium tabular-nums ${row.change_pct === 0 ? '' : up ? 'text-red-600' : 'text-emerald-600'}`} title="Contra el período anterior">
      {up ? '▲' : row.change_pct < 0 ? '▼' : ''} {Math.abs(row.change_pct).toFixed(1)}%
    </span>
  )
}

export function SettlementBreakdown() {
  const { range, channel, account } = useScope()
  const description = useScopeDescription()
  const applies = channel === 'all' || channel === 'tiktok_shop'
  const start = range.start ?? '2020-01-01'
  const end = range.end
  const store = channel === 'tiktok_shop' ? account : ''

  const data = useAsync(
    () => (applies ? settlements.breakdown({ start, end, account: store }) : Promise.resolve(null)),
    [applies, start, end, store],
  )
  const d = data.data
  const [picked, setPicked] = useState<number | null>(null)
  const statements = useMemo(() => d?.statements ?? [], [d?.statements])
  // Si la elegida ya no está en el período, se usa la más reciente.
  const selected = statements.find((s) => s.id === picked)?.id ?? statements[0]?.id ?? null

  const wf = useAsync(() => (selected == null ? Promise.resolve(null) : settlements.waterfall(selected)), [selected])
  const w = wf.data

  const steps = useMemo<WaterfallStep[]>(() => {
    if (!w) return []
    const sales = w.steps.find((s) => s.code === 'gross_sales')?.amount ?? 0
    return [
      { id: 'gross_sales', label: 'Ventas brutas', kind: 'total', value: sales },
      ...w.steps.filter((s) => s.code !== 'gross_sales').map((s) => ({ id: s.code, label: shortName(s.name), kind: 'delta' as const, value: s.amount })),
      { id: 'payout', label: 'Depósito', kind: 'total', value: w.net_payout },
    ]
  }, [w])

  const feeConcepts = useMemo(() => (d?.by_concept ?? []).filter((c) => c.kind === 'fee' && (c.cost > 0 || c.previous_cost > 0)), [d?.by_concept])
  const monthly = useMemo(
    () => (d?.by_month ?? []).map((m) => ({ month: m.month, fee_pct: m.fee_pct, ...Object.fromEntries(feeConcepts.map((c) => [c.code, m.concepts[c.code] ?? 0])) })),
    [d?.by_month, feeConcepts],
  )
  const series: ComboSeries[] = [
    ...feeConcepts.map((c, i) => ({ key: c.code, label: shortName(c.name), type: 'bar' as const, stack: 'fees', color: PALETTE[i % PALETTE.length], format: money })),
    { key: 'fee_pct', label: '% efectivo de comisión', type: 'line' as const, axis: 'right' as const, color: '#111827', format: pctText },
  ]
  const slices = (d?.by_concept ?? [])
    .filter((c) => (c.kind === 'fee' || c.kind === 'shipping') && c.cost > 0)
    .map((c) => ({ id: c.code, label: shortName(c.name), value: c.cost }))

  const sum = d?.summary
  const pts = sum?.fee_pct_change_points
  const empty = (msg: string) => <EmptyChart>{data.loading ? 'Cargando…' : msg}</EmptyChart>

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Desglose de la liquidación de TikTok</h1>
        <p className="text-muted-foreground text-xs">
          {description}
          {d && ` · del ${d.start} al ${d.end}, comparado con ${d.previous_start} a ${d.previous_end}`}
        </p>
      </div>

      {!applies && <Callout tone="info">Este desglose solo existe para TikTok Shop. Elige «Todos los canales» o TikTok Shop en el filtro de arriba.</Callout>}
      <ErrorAlert message={data.error} />
      {applies && data.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      {sum && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Kpi title="Ventas brutas" value={money(sum.sales)} hint={`${sum.statements} liquidaciones`} />
          <Kpi
            title="Comisiones"
            value={money(sum.fees)}
            hint={sum.fees_change_pct == null ? undefined : `${sum.fees_change_pct > 0 ? '+' : ''}${sum.fees_change_pct.toFixed(1)}% vs período anterior`}
            className={sum.fees_change_pct != null && sum.fees_change_pct > 0 ? 'text-red-600' : ''}
          />
          <Kpi
            title="% efectivo de comisión"
            value={pctText(sum.fee_pct)}
            hint={pts == null ? undefined : `${pts > 0 ? '+' : ''}${pts.toFixed(1)} pts vs ${pctText(sum.previous_fee_pct)}`}
            className={pts != null && pts > 0 ? 'text-red-600' : ''}
          />
          <Kpi
            title="Concepto más grande"
            value={sum.biggest_concept ? shortName(sum.biggest_concept.name) : '—'}
            hint={sum.biggest_concept ? `${money(sum.biggest_concept.cost)} · ${pctText(sum.biggest_concept.pct_of_sales)} de la venta` : undefined}
            className="text-lg"
          />
          <Kpi title="Depósitos netos" value={money(sum.net_payout)} hint={`Envío: ${money(sum.shipping)}`} />
        </div>
      )}

      <ChartCard
        title="Cascada de un depósito"
        subtitle="Ventas brutas → cada concepto → lo que TikTok depositó. Elige la liquidación."
        filename="liquidacion-cascada"
        csv={(w?.steps ?? []).map((s) => ({ Liquidacion: w?.external_id, Concepto: s.name, Monto: s.amount }))}
        actions={
          statements.length > 0 && (
            <select
              aria-label="Liquidación"
              value={selected ?? ''}
              onChange={(e) => setPicked(Number(e.target.value))}
              className="max-w-48 rounded-md border bg-white px-2 py-1 text-xs sm:max-w-none dark:bg-slate-900"
            >
              {statements.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.statement_date ?? 's/f'} · {money(s.net_payout)}
                </option>
              ))}
            </select>
          )
        }
      >
        {statements.length === 0 ? (
          empty('Sin liquidaciones con desglose en este período.')
        ) : wf.error ? (
          <ErrorAlert message={wf.error} />
        ) : !w ? (
          empty('Cargando…')
        ) : (
          <div className="space-y-2">
            <div className="text-muted-foreground text-xs">
              Liquidación {w.external_id}
              {w.status ? ` · ${w.status}` : ''} · comisiones {money(w.fees)} ({pctText(w.fee_pct)} de la venta)
            </div>
            <div className="overflow-x-auto">
              <div className="min-w-160">
                <Waterfall steps={steps} />
              </div>
            </div>
          </div>
        )}
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard
          className="xl:col-span-2"
          title="Comisiones por mes y concepto"
          subtitle="Barras apiladas: costo de cada comisión. Línea (eje derecho): % efectivo sobre la venta bruta. Clic en la leyenda para ocultar series."
          filename="liquidacion-mensual"
          csv={(d?.by_month ?? []).map((m) => ({ Mes: m.month, Ventas: m.sales, Comisiones: m.fees, '% efectivo': m.fee_pct, ...Object.fromEntries(feeConcepts.map((c) => [c.name, m.concepts[c.code] ?? 0])) }))}
        >
          {monthly.length === 0 ? (
            empty('Sin liquidaciones en este período.')
          ) : (
            <ComboChart data={monthly} xKey="month" xFormat={monthLabel} series={series} rightFormat={(v) => `${v.toFixed(0)}%`} />
          )}
        </ChartCard>

        <ChartCard
          title="¿Dónde se va el dinero?"
          subtitle="Comisiones y envío del período."
          filename="liquidacion-conceptos"
          csv={slices.map((s) => ({ Concepto: s.label, Costo: s.value }))}
        >
          {slices.length === 0 ? empty('Sin costos en este período.') : <Donut slices={slices} format={money} centerLabel="Total" />}
        </ChartCard>
      </div>

      <ChartCard
        title="Conceptos de la liquidación"
        subtitle="Variación contra el período anterior de la misma duración: en rojo si el costo crece."
        filename="liquidacion-conceptos-tabla"
        csv={(d?.by_concept ?? []).map((c) => ({
          Concepto: c.name,
          Tipo: KIND_LABEL[c.kind].label,
          Monto: c.amount,
          'Costo': c.cost,
          '% de la venta': c.pct_of_sales,
          'Costo periodo anterior': c.previous_cost,
          'Variacion %': c.change_pct,
        }))}
      >
        {(d?.by_concept ?? []).length === 0 ? (
          empty('Sin datos en este período.')
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Concepto</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead className="text-right">Monto</TableHead>
                  <TableHead className="text-right">% de la venta</TableHead>
                  <TableHead className="text-right">Período anterior</TableHead>
                  <TableHead className="text-right">Variación</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.by_concept ?? []).map((c) => (
                  <TableRow key={c.code} className={c.kind === 'memo' ? 'text-muted-foreground' : ''}>
                    <TableCell>{c.name}</TableCell>
                    <TableCell>
                      <Pill label={KIND_LABEL[c.kind].label} className={KIND_LABEL[c.kind].pill} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{money(c.amount)}</TableCell>
                    <TableCell className="text-right tabular-nums">{pctText(c.pct_of_sales)}</TableCell>
                    <TableCell className="text-right tabular-nums">{money(-c.previous_cost)}</TableCell>
                    <TableCell className="text-right">
                      <Change row={c} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </ChartCard>

      <Hint>
        Los montos son los de las transacciones de cada liquidación de TikTok (ingresos positivos, costos negativos). El % de la venta
        usa la venta bruta. «Promoción y campañas» agrupa las comisiones que la API de TikTok no desglosa en esta versión
        (Smart Promotion y cuotas de campaña). El impuesto de venta y el descuento de plataforma son informativos: no cambian el depósito.
      </Hint>
    </div>
  )
}
