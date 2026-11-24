// Portada de Walmart: KPIs, calidad, desempeño, pedidos por vencer, cargas con fallos y WFS agotados.
import { useMemo, type ReactNode } from 'react'

import { Callout, ErrorAlert } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { api, type AlertSeverity, type Filters } from '@/lib/api'
import { count, money, percent } from '@/lib/format'
import { rangeDays } from '@/lib/periodDays'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { walmartOrders, type OverviewPerformanceWindow, type WalmartOverviewResponse } from '@/lib/walmartOrders'
import { PageTitle } from '@/pages/walmart/walmartUi'

/** Ids de las pestañas del espacio Walmart a las que puede saltar la portada. */
type WalmartTab = 'orders' | 'openOrders' | 'inventory' | 'wfs' | 'performance' | 'quality' | 'feeds'
type Tone = 'plain' | 'good' | 'warn' | 'bad'

const PERFORMANCE_WINDOW_DAYS = 30
const LOW_QUALITY_SCORE = 80
const WALMART_CHANNEL = 'walmart'

const TONE_CLASS: Record<Tone, string> = {
  plain: '',
  good: 'text-emerald-600',
  warn: 'text-amber-600',
  bad: 'text-red-600',
}

const SEVERITY: Record<AlertSeverity, { label: string }> = {
  critical: { label: 'Críticas' },
  warning: { label: 'Advertencias' },
  info: { label: 'Informativas' },
}

const pct1 = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`)
const shortDay = (v: string | number) => String(v).slice(5)

/** Tarjeta de la portada; si hay `tab` y `onOpenTab`, es un botón que abre esa pestaña. */
function StatCard({
  title,
  value,
  hint,
  tone = 'plain',
  tab,
  onOpenTab,
}: {
  title: string
  value: string
  hint?: string
  tone?: Tone
  tab?: WalmartTab
  onOpenTab?: (tab: string) => void
}) {
  const body = (
    <CardContent className="space-y-1 pt-4 text-left">
      <div className="text-muted-foreground text-xs font-medium">{title}</div>
      <div className={`text-2xl font-semibold tabular-nums ${TONE_CLASS[tone]}`}>{value}</div>
      {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
    </CardContent>
  )
  if (!tab || !onOpenTab) return <Card>{body}</Card>
  return (
    <button
      type="button"
      onClick={() => onOpenTab(tab)}
      aria-label={`${title}: ${value}. Abrir detalle`}
      className="rounded-xl text-left transition-shadow hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
    >
      <Card className="h-full">{body}</Card>
    </button>
  )
}

interface Attention {
  id: string
  tone: 'bad' | 'warn'
  text: string
  tab: WalmartTab
  action: string
}

/** Lo más urgente, ordenado de mayor a menor gravedad. */
function attentionItems(d: WalmartOverviewResponse): Attention[] {
  const items: Attention[] = []
  const orders = d.open_orders
  if (orders?.available) {
    if (orders.overdue > 0)
      items.push({ id: 'overdue', tone: 'bad', tab: 'openOrders', action: 'Ver pedidos', text: `${count(orders.overdue)} pedido(s) con el plazo de envío vencido (${money(orders.sales_overdue)} en ventas).` })
    if (orders.due_soon > 0)
      items.push({ id: 'soon', tone: 'warn', tab: 'openOrders', action: 'Ver pedidos', text: `${count(orders.due_soon)} pedido(s) vencen en ${orders.due_soon_window_hours} h (${money(orders.sales_due_soon)} en ventas).` })
  }
  if (d.feeds_with_failures > 0)
    items.push({ id: 'feeds', tone: 'bad', tab: 'feeds', action: 'Ver cargas', text: `${count(d.feeds_with_failures)} carga(s) con fallos en los últimos ${d.feed_failure_window_days} días.` })
  const score = d.listing_quality?.score
  if (score != null && score < LOW_QUALITY_SCORE)
    items.push({ id: 'quality', tone: 'warn', tab: 'quality', action: 'Ver calidad', text: `La calidad del listado es ${score.toFixed(1)}, por debajo de ${LOW_QUALITY_SCORE}.` })
  if (d.wfs_out_of_stock_skus > 0)
    items.push({ id: 'wfs', tone: 'warn', tab: 'wfs', action: 'Ver WFS', text: `${count(d.wfs_out_of_stock_skus)} SKU de WFS sin existencias.` })
  return items
}

function AttentionList({ items, onOpenTab }: { items: Attention[]; onOpenTab?: (tab: string) => void }) {
  return (
    <ChartCard title="Qué atender hoy" subtitle="Lo más urgente de Walmart ahora mismo.">
      {items.length === 0 ? (
        <p className="text-muted-foreground py-6 text-center text-sm">Todo en orden: no hay nada urgente.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((i) => (
            <li
              key={i.id}
              className={`flex flex-wrap items-center justify-between gap-2 rounded-md border-l-4 bg-slate-50 p-2 text-sm dark:bg-slate-900 ${i.tone === 'bad' ? 'border-l-red-500' : 'border-l-amber-500'}`}
            >
              <span>{i.text}</span>
              {onOpenTab && (
                <button type="button" onClick={() => onOpenTab(i.tab)} className="rounded border px-2 py-1 text-xs hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                  {i.action}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </ChartCard>
  )
}

function performanceWindow(d: WalmartOverviewResponse): OverviewPerformanceWindow | undefined {
  const windows = d.seller_performance.windows
  return windows.find((w) => w.window_days === PERFORMANCE_WINDOW_DAYS) ?? windows[0]
}

function Section({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
}

export function WalmartOverview({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  const scope = useScope()
  const description = useScopeDescription()
  const days = rangeDays(scope.range)
  const filters = useMemo<Filters>(
    () => ({ ...scope.range, channel: scope.channelParam, account: scope.account }),
    [scope.range, scope.channelParam, scope.account],
  )
  const overview = useAsync(() => walmartOrders.overview(days), [days])
  const series = useAsync(() => api.timeseries('day', filters), [filters])
  const alerts = useAsync(() => api.alerts(), [])

  const d = overview.data
  const perf = d ? performanceWindow(d) : undefined
  const orders = d?.open_orders
  const score = d?.listing_quality?.score ?? null

  const daily = useMemo(
    () => (series.data?.rows ?? []).map((r) => ({ date: r.period ?? '', ventas: r.gross_sales, utilidad: r.net_profit })),
    [series.data],
  )

  const alertSlices = useMemo<DonutSlice[]>(() => {
    const walmart = (alerts.data?.alerts ?? []).filter((a) => a.channel === WALMART_CHANNEL)
    return (Object.keys(SEVERITY) as AlertSeverity[]).map((s) => ({
      id: s,
      label: SEVERITY[s].label,
      value: walmart.filter((a) => a.severity === s).length,
    }))
  }, [alerts.data])

  const qualityTone: Tone = score == null ? 'plain' : score < LOW_QUALITY_SCORE ? 'bad' : 'good'
  const overdue = orders?.available ? orders.overdue : 0
  const soon = orders?.available ? orders.due_soon : 0

  return (
    <div className="space-y-6">
      <PageTitle
        title="Walmart: resumen"
        scope={description}
        note="Ventas y utilidad siguen el período elegido; pedidos, calidad y cargas muestran el estado actual."
      />
      <ErrorAlert message={overview.error} />
      {overview.loading && !d && <p className="text-muted-foreground text-sm">Cargando…</p>}
      {orders && !orders.available && (
        <Callout tone="warning">
          Walmart no respondió, así que los pedidos por surtir no están disponibles ({orders.error}). El resto de la
          portada sigue con los datos guardados.
        </Callout>
      )}

      {d && (
        <>
          <Section>
            <StatCard title="Ventas" value={money(d.kpis.gross_sales)} hint={`${count(d.kpis.units)} unidades`} tab="orders" onOpenTab={onOpenTab} />
            <StatCard
              title="Utilidad neta"
              value={money(d.kpis.net_profit)}
              tone={d.kpis.net_profit < 0 ? 'bad' : 'plain'}
              hint={`Margen ${percent(d.kpis.margin)}`}
              tab="orders"
              onOpenTab={onOpenTab}
            />
            <StatCard title="Órdenes" value={count(d.kpis.orders)} hint={`Últimos ${d.days} días`} tab="orders" onOpenTab={onOpenTab} />
            <StatCard
              title="Calidad del listado"
              value={score == null ? '—' : score.toFixed(1)}
              tone={qualityTone}
              hint={d.listing_quality ? `Al ${d.listing_quality.snapshot_date}` : 'Sin datos todavía'}
              tab="quality"
              onOpenTab={onOpenTab}
            />
          </Section>
          <Section>
            <StatCard
              title={`Entregado a tiempo (${perf?.window_days ?? PERFORMANCE_WINDOW_DAYS} d)`}
              value={pct1(perf?.otd)}
              hint={perf ? `Enviado a tiempo ${pct1(perf.ots)} · Guía válida ${pct1(perf.vtr)} · Cancelaciones ${pct1(perf.cancellation_seller_fault)}` : 'Sin datos todavía'}
              tab="performance"
              onOpenTab={onOpenTab}
            />
            <StatCard
              title="Pedidos vencidos o por vencer"
              value={orders?.available ? count(overdue + soon) : '—'}
              tone={overdue > 0 ? 'bad' : soon > 0 ? 'warn' : 'plain'}
              hint={orders?.available ? `${count(overdue)} vencidos · ${count(soon)} por vencer en ${orders.due_soon_window_hours} h` : 'Walmart no respondió'}
              tab="openOrders"
              onOpenTab={onOpenTab}
            />
            <StatCard
              title="Cargas con fallos"
              value={count(d.feeds_with_failures)}
              tone={d.feeds_with_failures > 0 ? 'bad' : 'good'}
              hint={`Últimos ${d.feed_failure_window_days} días`}
              tab="feeds"
              onOpenTab={onOpenTab}
            />
            <StatCard
              title="SKU de WFS agotados"
              value={count(d.wfs_out_of_stock_skus)}
              tone={d.wfs_out_of_stock_skus > 0 ? 'warn' : 'good'}
              tab="wfs"
              onOpenTab={onOpenTab}
            />
          </Section>

          <ChartCard
            title="Ventas y utilidad diaria"
            subtitle="Walmart en el período elegido."
            csv={daily.map((r) => ({ Fecha: r.date, Ventas: r.ventas, Utilidad: r.utilidad }))}
            filename="walmart-ventas-utilidad-diaria"
          >
            {series.error ? (
              <ErrorAlert message={series.error} />
            ) : (
              <ComboChart
                data={daily}
                xKey="date"
                xFormat={shortDay}
                series={[
                  { key: 'ventas', label: 'Ventas', type: 'bar', color: COLOR.blue, format: money },
                  { key: 'utilidad', label: 'Utilidad', type: 'line', color: COLOR.good, format: money },
                ]}
              />
            )}
          </ChartCard>

          <div className="grid gap-4 lg:grid-cols-2">
            <AttentionList items={attentionItems(d)} onOpenTab={onOpenTab} />
            <ChartCard title="Alertas pendientes" subtitle="Alertas activas de Walmart por gravedad.">
              {alerts.error ? (
                <EmptyChart>No se pudieron cargar las alertas.</EmptyChart>
              ) : (
                <Donut slices={alertSlices} format={count} height={200} centerLabel="alertas" />
              )}
            </ChartCard>
          </div>
        </>
      )}
    </div>
  )
}
