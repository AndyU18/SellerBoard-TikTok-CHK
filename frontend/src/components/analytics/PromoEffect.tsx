import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { ErrorAlert, Hint, Kpi, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart, axisNumber, axisPct } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics, type PromoVerdict } from '@/lib/analytics'
import { chartdata, type PromoPeriod } from '@/lib/chartdata_precios'
import { count, money, tone } from '@/lib/format'
import { rangeDays } from '@/lib/periodDays'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const VERDICT: Record<PromoVerdict, { label: string; pill: string }> = {
  worth_it: { label: 'Vale la pena', pill: PILL.green },
  not_worth: { label: 'No compensa', pill: PILL.red },
  insufficient: { label: 'Pocos días', pill: PILL.slate },
}

/** ¿Las promociones aumentaron las ventas y la utilidad? */
export function PromoEffect() {
  const scope = useScope()
  const [verdict, setVerdict] = useState<PromoVerdict | 'all'>('all')
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const days = rangeDays(scope.range)
  const data = useAsync(() => analytics.promoEffect(sc, days), [sc, days])
  const timeline = useAsync(
    () => chartdata.promoTimeline(days, scope.channelParam, scope.account),
    [days, scope.channelParam, scope.account],
  )
  const [picked, setPicked] = useState<string | null>(null)
  const d = data.data
  const periods = useMemo(() => timeline.data?.periods ?? [], [timeline.data])
  const periodId = (p: PromoPeriod) => `${p.start}_${p.end}`
  const periodLabel = (p: PromoPeriod) => `${p.start.slice(5)} → ${p.end.slice(5)} · ${(p.avg_depth * 100).toFixed(0)}%`
  const selected = periods.find((p) => periodId(p) === picked) ?? null

  // Línea de tiempo: ventas y unidades por día, barras de descuento y sombreado antes/durante/después.
  const maxUnits = useMemo(() => Math.max(1, ...(timeline.data?.series ?? []).map((x) => x.units)), [timeline.data])
  const timelineRows = useMemo(() => {
    const series = timeline.data?.series ?? []
    const idx = new Map(series.map((x, i) => [x.date, i]))
    const near = (start: string, end: string, side: 'before' | 'after') => {
      const i = idx.get(side === 'before' ? start : end)
      if (i == null) return new Set<string>()
      const slice = side === 'before' ? series.slice(Math.max(0, i - 7), i) : series.slice(i + 1, i + 8)
      return new Set(slice.filter((x) => !x.promo).map((x) => x.date))
    }
    const before = selected ? near(selected.start, selected.end, 'before') : new Set<string>()
    const after = selected ? near(selected.start, selected.end, 'after') : new Set<string>()
    return series.map((x) => ({
      date: x.date,
      sales: x.net_sales,
      units: x.units,
      discount: x.promo ? +(x.depth * 100).toFixed(1) : null,
      during: x.promo ? maxUnits : null,
      before: before.has(x.date) ? maxUnits : null,
      after: after.has(x.date) ? maxUnits : null,
    }))
  }, [timeline.data, selected, maxUnits])
  const timelineSeries: ComboSeries[] = [
    { key: 'discount', label: 'Descuento en días de promoción (%)', type: 'bar', axis: 'right', color: COLOR.amber, format: (v) => `${v.toFixed(1)}%` },
    { key: 'sales', label: 'Ventas netas por día', type: 'line', color: '#2563eb' },
    { key: 'units', label: 'Unidades por día', type: 'line', axis: 'right', color: '#059669', format: (v) => v.toFixed(0) },
    { key: 'during', label: 'Durante la promoción', type: 'area', axis: 'right', color: '#f59e0b', format: () => 'sí' },
    ...(selected
      ? ([
          { key: 'before', label: 'Antes (7 días)', type: 'area', axis: 'right', color: '#64748b', format: () => 'sí' },
          { key: 'after', label: 'Después (7 días)', type: 'area', axis: 'right', color: '#8b5cf6', format: () => 'sí' },
        ] as ComboSeries[])
      : []),
  ]

  const ranking = useMemo<HBarRow[]>(
    () =>
      periods
        .filter((p) => p.uplift != null)
        .map((p) => ({ id: periodId(p), label: periodLabel(p), uplift: p.uplift as number }))
        .sort((a, b) => Number(b.uplift) - Number(a.uplift))
        .slice(0, 15),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [periods],
  )
  const bubbles = useMemo<BubblePoint[]>(
    () =>
      periods
        .filter((p) => p.uplift != null)
        .map((p) => ({
          id: periodId(p),
          label: periodLabel(p),
          x: p.avg_depth,
          y: p.uplift as number,
          z: Math.max(1, (p.during.units_per_day ?? 0) * p.days),
        })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [periods],
  )
  const rows = (d?.rows ?? []).filter((r) => verdict === 'all' || r.verdict === verdict)

  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">¿Las promociones funcionan? (últimos {days} días)</h2>
      <ErrorAlert message={data.error} />
      <ErrorAlert message={timeline.error} />
      <div className="grid gap-4 md:grid-cols-3">
        <Kpi
          title="Vale la pena"
          value={count(d?.summary.worth_it ?? 0)}
          hint="más utilidad por día con promoción"
          className="text-green-700"
        />
        <Kpi
          title="No compensa"
          value={count(d?.summary.not_worth ?? 0)}
          hint="menos utilidad por día con promoción"
          className={(d?.summary.not_worth ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi
          title="Sin veredicto"
          value={count(d?.summary.insufficient ?? 0)}
          hint="menos de 3 días con o sin promoción"
        />
      </div>

      <ChartCard
        title="Línea de tiempo: ventas, unidades y descuento"
        subtitle="Días con promoción sombreados. Clic en una promoción del ranking para marcar los 7 días de antes y de después."
        filename="promociones-linea-de-tiempo"
        csv={(timeline.data?.series ?? []).map((x) => ({
          fecha: x.date,
          ventas_netas: x.net_sales,
          unidades: x.units,
          descuento_pct: +(x.depth * 100).toFixed(2),
          en_promocion: x.promo ? 'sí' : 'no',
        }))}
      >
        {timeline.loading && !timeline.data ? (
          <EmptyChart>Cargando…</EmptyChart>
        ) : (
          <ComboChart
            data={timelineRows}
            xKey="date"
            xFormat={(v) => String(v).slice(5)}
            series={timelineSeries}
            rightFormat={axisNumber}
            onPointClick={(row) => {
              const date = String(row.date)
              const p = periods.find((x) => date >= x.start && date <= x.end)
              if (p) setPicked(periodId(p))
            }}
          />
        )}
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Ranking de promociones por efecto"
          subtitle="Unidades por día durante la promoción frente a los días vecinos sin promoción. Clic para ver el detalle."
          filename="promociones-ranking"
          csv={periods.map((p) => ({
            inicio: p.start,
            fin: p.end,
            dias: p.days,
            descuento_prom: p.avg_depth,
            unidades_dia_antes: p.before.units_per_day,
            unidades_dia_durante: p.during.units_per_day,
            unidades_dia_despues: p.after.units_per_day,
            efecto_unidades: p.uplift,
          }))}
        >
          {timeline.loading && !timeline.data ? (
            <EmptyChart>Cargando…</EmptyChart>
          ) : ranking.length === 0 ? (
            <EmptyChart>No hubo promociones con días vecinos para comparar.</EmptyChart>
          ) : (
            <HBarChart
              rows={ranking}
              series={[{ key: 'uplift', label: 'Efecto en unidades' }]}
              format={axisPct}
              refLine={0}
              labelWidth={120}
              rowColor={(r) => (Number(r.uplift) < 0 ? COLOR.bad : COLOR.good)}
              selectedId={picked}
              onRowClick={(r) => setPicked(r.id)}
            />
          )}
        </ChartCard>
        <ChartCard
          title="Descuento vs. efecto en unidades"
          subtitle="Cada burbuja es una promoción; el tamaño son las unidades vendidas durante ella."
          filename="promociones-descuento-vs-efecto"
          csv={bubbles.map((b) => ({ promocion: b.label, descuento: b.x, efecto_unidades: b.y, unidades: b.z }))}
        >
          {timeline.loading && !timeline.data ? (
            <EmptyChart>Cargando…</EmptyChart>
          ) : (
            <BubbleChart
              points={bubbles}
              xLabel="Descuento promedio"
              yLabel="Efecto en unidades"
              zLabel="Unidades"
              xFormat={axisPct}
              yFormat={axisPct}
              zFormat={(v) => v.toFixed(0)}
              yRef={0}
              onPointClick={(pt) => setPicked(pt.id)}
            />
          )}
        </ChartCard>
      </div>
      {selected && (
        <Card>
          <CardContent className="space-y-2 pt-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong>
                Promoción del {selected.start} al {selected.end} ({selected.days} días, descuento {pct1(selected.avg_depth)})
              </strong>
              <button type="button" className="text-muted-foreground text-xs underline" onClick={() => setPicked(null)}>
                Cerrar
              </button>
            </div>
            <div className="grid gap-x-6 gap-y-1 sm:grid-cols-3">
              {(
                [
                  ['Antes', selected.before],
                  ['Durante', selected.during],
                  ['Después', selected.after],
                ] as const
              ).map(([name, w]) => (
                <div key={name}>
                  <div className="text-muted-foreground text-xs">
                    {name} ({w.days} días)
                  </div>
                  <div>
                    {w.units_per_day == null ? '—' : `${w.units_per_day.toFixed(1)} unid./día`} ·{' '}
                    {money(w.sales_per_day)}/día
                  </div>
                </div>
              ))}
            </div>
            <div>
              Efecto en unidades: <strong className={tone(selected.uplift)}>{pct1(selected.uplift)}</strong>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Por profundidad del descuento</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Canal</TableHead>
                <TableHead>Descuento</TableHead>
                <TableHead className="text-right">Días-producto</TableHead>
                <TableHead className="text-right">Índice de unidades</TableHead>
                <TableHead className="text-right">Utilidad por día-producto</TableHead>
                <TableHead className="text-right">Margen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(d?.buckets ?? []).map((b) => (
                <TableRow key={`${b.channel}-${b.bucket}`}>
                  <TableCell>
                    <ChannelBadge channel={b.channel} />
                  </TableCell>
                  <TableCell>{b.bucket}</TableCell>
                  <TableCell className="text-right">{count(b.sku_days)}</TableCell>
                  <TableCell className="text-right">{b.units_index.toFixed(2)}×</TableCell>
                  <TableCell className={`text-right ${tone(b.profit_per_sku_day)}`}>
                    {money(b.profit_per_sku_day)}
                  </TableCell>
                  <TableCell className={`text-right ${tone(b.margin)}`}>{pct1(b.margin)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Segmented
        label="Veredicto"
        value={verdict}
        options={[
          { value: 'all', label: 'Todos' },
          { value: 'not_worth', label: 'No compensa', count: d?.summary.not_worth },
          { value: 'worth_it', label: 'Vale la pena', count: d?.summary.worth_it },
          { value: 'insufficient', label: 'Pocos días', count: d?.summary.insufficient },
        ]}
        onChange={setVerdict}
      />
      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Días promo / normal</TableHead>
                <TableHead className="text-right">Descuento prom.</TableHead>
                <TableHead className="text-right">Unid./día promo → normal</TableHead>
                <TableHead className="text-right">Más ventas</TableHead>
                <TableHead className="text-right">Utilidad/día promo → normal</TableHead>
                <TableHead>Veredicto</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground py-8 text-center">
                    {data.loading ? 'Cargando…' : 'Sin productos con este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.slice(0, 40).map((r) => (
                <TableRow key={`${r.channel}-${r.sku}`}>
                  <TableCell>
                    <div className="flex min-w-44 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground flex items-center gap-2 text-xs">
                        {r.sku} <ChannelBadge channel={r.channel} />
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    {r.promo_days} / {r.normal_days}
                  </TableCell>
                  <TableCell className="text-right">{pct1(r.avg_depth)}</TableCell>
                  <TableCell className="text-right">
                    {r.units_per_day_promo.toFixed(1)} → {r.units_per_day_normal.toFixed(1)}
                  </TableCell>
                  <TableCell className={`text-right ${tone(r.uplift)}`}>{pct1(r.uplift)}</TableCell>
                  <TableCell className="text-right">
                    {money(r.profit_per_day_promo)} → {money(r.profit_per_day_normal)}
                  </TableCell>
                  <TableCell>
                    <Pill label={VERDICT[r.verdict].label} className={VERDICT[r.verdict].pill} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Hint>
        Un día cuenta como “con promoción” si descontaste {pct1(d?.promo_threshold)} o más del precio de lista. Se
        compara la utilidad por día con y sin promoción de cada producto (los días sin ventas cuentan como cero). El
        índice de unidades es cuántas unidades se venden ese día frente al promedio normal del mismo producto. Es una
        comparación observacional: no separa el efecto del descuento del de la temporada, la publicidad o los
        creadores.
      </Hint>
    </section>
  )
}
