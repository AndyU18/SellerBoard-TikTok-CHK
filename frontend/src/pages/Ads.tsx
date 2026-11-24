import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Callout, ErrorAlert, Hint, Kpi, pct1 } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { Donut } from '@/components/charts/Donut'
import { HeatGrid } from '@/components/charts/HeatGrid'
import { axisPct, ChartCard, COLOR, EmptyChart, TipBox } from '@/components/charts/kit'
import { Button } from '@/components/ui/button'
import { ads } from '@/lib/ads'
import { count, money, tone } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const WEEKDAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
const shortDay = (v: string | number) => String(v).slice(5)
const ratio = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}x`)
/** Referencia de TACOS: por encima de esto la publicidad se come demasiado de la venta. */
const TACOS_TARGET = 0.15

export function Ads() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const [tick, setTick] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)
  const data = useAsync(() => ads.overview(days), [days, tick])
  const d = data.data
  const k = d?.kpis
  const daily = d?.daily ?? []
  const loading = data.loading && !d

  const sync = async () => {
    setSyncing(true)
    setSyncMsg(null)
    try {
      await ads.sync(days)
      setTick((t) => t + 1)
    } catch (e) {
      setSyncMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setSyncing(false)
    }
  }

  const tacosSeries = daily.filter((r) => r.tacos != null).map((r) => ({ date: r.date, tacos: r.tacos as number }))
  const hasWeekday = (d?.by_weekday ?? []).some((v) => v > 0)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Publicidad (TikTok Ads)</h1>
          {d && (
            <p className="text-muted-foreground text-xs">
              Del {d.window.start} al {d.window.end}
              {d.last_sync ? ` · última sincronización: ${d.last_sync.slice(0, 16).replace('T', ' ')}` : ' · sin sincronizaciones'}
            </p>
          )}
        </div>
        <Button variant="outline" onClick={sync} disabled={syncing}>
          <RefreshCw className={`mr-2 size-4 ${syncing ? 'animate-spin' : ''}`} />
          {syncing ? 'Sincronizando…' : 'Sincronizar ahora'}
        </Button>
      </div>

      <Hint>Usa el período global de la barra superior. ROAS = ventas ÷ gasto; TACOS = gasto ÷ ventas del canal.</Hint>
      <ErrorAlert message={data.error} />
      {syncMsg && <Callout tone="danger">No se pudo sincronizar TikTok Ads: {syncMsg}</Callout>}
      {loading && <div className="text-muted-foreground text-sm">Cargando…</div>}

      {d && !d.has_spend && (
        <Callout>
          La cuenta conectada (Victoria Cookware) no tiene gasto en los últimos 90 días; si tu publicidad está en otro
          anunciante hay que autorizarlo de nuevo.
        </Callout>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Gasto en publicidad" value={money(k?.spend ?? 0)} hint={`${count(k?.orders ?? 0)} pedidos del canal`} />
        <Kpi title="Ventas del canal" value={money(k?.sales ?? 0)} hint="TikTok Shop, ventas brutas" />
        <Kpi title="ROAS" value={ratio(k?.roas)} hint="ventas por cada $1 de publicidad" />
        <Kpi
          title="TACOS"
          value={pct1(k?.tacos)}
          hint={`referencia ${(TACOS_TARGET * 100).toFixed(0)}%`}
          className={(k?.tacos ?? 0) > TACOS_TARGET ? 'text-amber-600' : ''}
        />
        <Kpi title="Gasto por pedido" value={money(k?.spend_per_order)} hint="gasto ÷ pedidos" />
        <Kpi
          title="Utilidad antes de publicidad"
          value={money(k?.profit_before_ads ?? 0)}
          hint="ventas − promos − comisiones − envío − devoluciones − costo"
          className={tone(k?.profit_before_ads)}
        />
        <Kpi
          title="Utilidad después de publicidad"
          value={money(k?.profit_after_ads ?? 0)}
          hint="lo anterior menos el gasto en anuncios"
          className={tone(k?.profit_after_ads)}
        />
      </div>

      <ChartCard
        title="Gasto, ventas y ROAS por día"
        subtitle="Barras: gasto. Líneas: ventas (eje izquierdo) y ROAS (eje derecho)."
        filename="publicidad-diario"
        csv={daily.map((r) => ({ fecha: r.date, gasto: r.spend, ventas: r.sales, pedidos: r.orders, roas: r.roas }))}
      >
        {loading ? (
          <EmptyChart>Cargando…</EmptyChart>
        ) : (
          <ComboChart
            data={daily}
            xKey="date"
            xFormat={shortDay}
            rightFormat={(v) => `${v.toFixed(1)}x`}
            series={[
              { key: 'spend', label: 'Gasto', type: 'bar', color: COLOR.amber, format: money },
              { key: 'sales', label: 'Ventas', type: 'line', color: COLOR.blue, format: money },
              { key: 'roas', label: 'ROAS', type: 'line', axis: 'right', color: COLOR.good, format: ratio },
            ]}
          />
        )}
      </ChartCard>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="TACOS por día"
          subtitle={`La línea punteada marca la referencia de ${(TACOS_TARGET * 100).toFixed(0)}%.`}
          filename="publicidad-tacos"
          csv={tacosSeries.map((r) => ({ fecha: r.date, tacos: r.tacos }))}
        >
          {tacosSeries.length === 0 ? (
            <EmptyChart>{loading ? 'Cargando…' : 'Sin ventas en este período.'}</EmptyChart>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={tacosSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tickFormatter={shortDay} tick={{ fontSize: 11 }} />
                <YAxis tickFormatter={axisPct} tick={{ fontSize: 11 }} width={44} />
                <Tooltip
                  content={({ active, payload, label }) =>
                    active && payload?.length ? (
                      <TipBox title={String(label)} rows={[{ label: 'TACOS', value: pct1(Number(payload[0].value)), color: COLOR.amber }]} />
                    ) : null
                  }
                />
                <ReferenceLine
                  y={TACOS_TARGET}
                  stroke={COLOR.bad}
                  strokeDasharray="5 4"
                  label={{ value: 'Referencia', fontSize: 10, fill: COLOR.bad, position: 'insideTopRight' }}
                />
                <Line type="monotone" dataKey="tacos" stroke={COLOR.amber} strokeWidth={2} dot={{ r: 3, fill: '#fff', strokeWidth: 2 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard
          title="Gasto por cuenta"
          filename="publicidad-por-cuenta"
          csv={(d?.by_account ?? []).map((r) => ({ cuenta: r.account, gasto: r.spend }))}
        >
          <Donut
            slices={(d?.by_account ?? []).map((r) => ({ id: r.account, label: r.account, value: r.spend }))}
            format={money}
            centerLabel="gasto total"
          />
        </ChartCard>
      </div>

      <ChartCard
        title="Gasto por día de la semana"
        subtitle="Cuánto se gasta en cada día; más oscuro = más gasto."
        filename="publicidad-dia-semana"
        csv={(d?.by_weekday ?? []).map((v, i) => ({ dia: WEEKDAYS[i], gasto: v }))}
      >
        {hasWeekday ? (
          <HeatGrid rowLabels={['Gasto']} colLabels={WEEKDAYS.map((w) => w.slice(0, 3))} values={[d?.by_weekday ?? []]} format={money} />
        ) : (
          <EmptyChart>{loading ? 'Cargando…' : 'Sin gasto en este período.'}</EmptyChart>
        )}
      </ChartCard>

      <ChartCard
        title="Gasto y ventas por mes"
        filename="publicidad-mensual"
        csv={(d?.monthly ?? []).map((r) => ({ mes: r.month, gasto: r.spend, ventas: r.sales, pedidos: r.orders, roas: r.roas, tacos: r.tacos }))}
      >
        <ComboChart
          data={d?.monthly ?? []}
          xKey="month"
          series={[
            { key: 'spend', label: 'Gasto', type: 'bar', color: COLOR.amber, format: money },
            { key: 'sales', label: 'Ventas', type: 'bar', color: COLOR.blue, format: money },
            { key: 'roas', label: 'ROAS', type: 'line', axis: 'right', color: COLOR.good, format: ratio },
          ]}
          rightFormat={(v) => `${v.toFixed(1)}x`}
          height={260}
        />
      </ChartCard>
    </div>
  )
}
