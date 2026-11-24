// Portada de TikTok Shop: ventas, utilidad, órdenes, y lo que necesita atención (rechazos, paquetes, pagos).
import { useMemo, type ReactNode } from 'react'

import { Callout, ErrorAlert } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { actions, type ActionItem } from '@/lib/actions'
import { api, type Filters } from '@/lib/api'
import { count, money, percent } from '@/lib/format'
import { rangeDays } from '@/lib/periodDays'
import { useScope } from '@/lib/scope'
import { tiktokOverview, type TikTokOverviewResponse } from '@/lib/tiktokOverview'
import { useAsync } from '@/lib/useAsync'
import { PageTitle } from '@/pages/walmart/walmartUi'

/** Ids de las pestañas del espacio TikTok a las que puede saltar la portada. */
type TikTokTab = 'orders' | 'catalog' | 'packages' | 'payments'
type Tone = 'plain' | 'good' | 'warn' | 'bad'

const ACTION_KIND_PREFIX = 'tiktok'
const MAX_ACTIONS_SHOWN = 8
/** Pestaña donde se resuelve cada tipo de acción del Centro de acciones de TikTok. */
const ACTION_TAB: Record<string, TikTokTab> = {
  tiktok_listing_rejected: 'catalog',
  tiktok_packages_stuck: 'packages',
  tiktok_payment_issue: 'payments',
}

const TONE_CLASS: Record<Tone, string> = {
  plain: '',
  good: 'text-emerald-600',
  warn: 'text-amber-600',
  bad: 'text-red-600',
}

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
  tab?: TikTokTab
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

function OpenTabButton({ tab, label, onOpenTab }: { tab: TikTokTab; label: string; onOpenTab?: (tab: string) => void }) {
  if (!onOpenTab) return null
  return (
    <button
      type="button"
      onClick={() => onOpenTab(tab)}
      className="rounded border px-2 py-1 text-xs hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
    >
      {label}
    </button>
  )
}

interface Attention {
  id: string
  tone: 'bad' | 'warn'
  text: string
  tab: TikTokTab
  action: string
}

/** Lo más urgente, de mayor a menor gravedad. */
function attentionItems(d: TikTokOverviewResponse): Attention[] {
  const items: Attention[] = []
  const { rejected_products: rej, stuck_packages: stuck, payments: pay } = d
  if (rej.count > 0) {
    const sales = rej.with_sales > 0 ? ` ${count(rej.with_sales)} tuvieron ventas (${money(rej.sales_usd)}).` : ''
    items.push({ id: 'rejected', tone: 'bad', tab: 'catalog', action: 'Ver catálogo', text: `${count(rej.count)} producto(s) rechazado(s) o con problemas.${sales}` })
  }
  if (stuck.count > 0)
    items.push({ id: 'stuck', tone: 'bad', tab: 'packages', action: 'Ver paquetes', text: `${count(stuck.count)} paquete(s) atascado(s) por más de ${stuck.stuck_days} días.` })
  const rec = pay.reconciliation
  const unreconciled = rec.checkable - rec.reconciled
  if (unreconciled > 0)
    items.push({ id: 'unreconciled', tone: 'warn', tab: 'payments', action: 'Ver pagos', text: `${count(unreconciled)} pago(s) no cuadran con sus liquidaciones (diferencia ${money(rec.difference)}).` })
  if (pay.pending > 0)
    items.push({ id: 'pending', tone: 'warn', tab: 'payments', action: 'Ver pagos', text: `${count(pay.pending)} pago(s) sin completar por ${money(pay.pending_amount)}.` })
  return items
}

function AttentionList({ items, onOpenTab }: { items: Attention[]; onOpenTab?: (tab: string) => void }) {
  return (
    <ChartCard title="Qué atender hoy" subtitle="Lo más urgente de TikTok Shop ahora mismo.">
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
              <OpenTabButton tab={i.tab} label={i.action} onOpenTab={onOpenTab} />
            </li>
          ))}
        </ul>
      )}
    </ChartCard>
  )
}

function ActionAlerts({
  items,
  loading,
  error,
  onOpenTab,
}: {
  items: ActionItem[]
  loading: boolean
  error: string | null
  onOpenTab?: (tab: string) => void
}) {
  const shown = items.slice(0, MAX_ACTIONS_SHOWN)
  return (
    <ChartCard title="Alertas del Centro de acciones" subtitle="Acciones abiertas de TikTok Shop, de mayor a menor impacto estimado.">
      {error ? (
        <EmptyChart>No se pudieron cargar las acciones.</EmptyChart>
      ) : shown.length === 0 ? (
        <EmptyChart>{loading ? 'Cargando…' : 'No hay acciones abiertas de TikTok Shop.'}</EmptyChart>
      ) : (
        <ul className="space-y-2">
          {shown.map((a) => {
            const tab = ACTION_TAB[a.kind]
            return (
              <li key={a.key} className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-slate-50 p-2 text-sm dark:bg-slate-900">
                <div className="min-w-0">
                  <div className="font-medium">{a.title}</div>
                  <div className="text-muted-foreground text-xs">
                    {a.kind_label} · Impacto estimado {money(a.impact_usd)}
                    {a.impact_basis ? ` (${a.impact_basis})` : ''}
                  </div>
                </div>
                {tab && <OpenTabButton tab={tab} label="Resolver" onOpenTab={onOpenTab} />}
              </li>
            )
          })}
        </ul>
      )}
      {items.length > MAX_ACTIONS_SHOWN && (
        <p className="text-muted-foreground mt-2 text-xs">
          Y {count(items.length - MAX_ACTIONS_SHOWN)} más en el Centro de acciones.
        </p>
      )}
    </ChartCard>
  )
}

function Section({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
}

export function TikTokOverview({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  const scope = useScope()
  const description = useScopeDescription()
  const days = rangeDays(scope.range)
  const filters = useMemo<Filters>(
    () => ({ ...scope.range, channel: scope.channelParam, account: scope.account }),
    [scope.range, scope.channelParam, scope.account],
  )
  const overview = useAsync(() => tiktokOverview.get(days), [days])
  const series = useAsync(() => api.timeseries('day', filters), [filters])
  const openActions = useAsync(() => actions.list('open'), [])

  const d = overview.data
  const rec = d?.payments.reconciliation
  const receivable = d ? d.receivable.unpaid_settlements_amount + d.receivable.pending_payments_amount : 0
  const margin = d && d.kpis.sales > 0 ? d.kpis.net_profit / d.kpis.sales : null

  const daily = useMemo(
    () => (series.data?.rows ?? []).map((r) => ({ date: r.period ?? '', ventas: r.gross_sales, utilidad: r.net_profit })),
    [series.data],
  )
  const tiktokActions = useMemo(
    () =>
      (openActions.data?.items ?? [])
        .filter((a) => a.kind.startsWith(ACTION_KIND_PREFIX))
        .sort((a, b) => b.impact_usd - a.impact_usd),
    [openActions.data],
  )

  return (
    <div className="space-y-6">
      <PageTitle
        title="TikTok Shop: resumen"
        scope={description}
        note="Ventas y utilidad siguen el período elegido; productos, paquetes y pagos muestran el estado actual."
      />
      <ErrorAlert message={overview.error} />
      {overview.loading && !d && <p className="text-muted-foreground text-sm">Cargando…</p>}
      {d && !d.last_sync_at && (
        <Callout tone="info">Todavía no hay una sincronización completa de datos extra de TikTok Shop; algunos bloques pueden aparecer vacíos.</Callout>
      )}

      {d && (
        <>
          <Section>
            <StatCard title="Ventas" value={money(d.kpis.sales)} hint={`${count(d.kpis.units)} unidades`} tab="orders" onOpenTab={onOpenTab} />
            <StatCard
              title="Utilidad neta"
              value={money(d.kpis.net_profit)}
              tone={d.kpis.net_profit < 0 ? 'bad' : 'plain'}
              hint={`Margen ${percent(margin)}`}
              tab="orders"
              onOpenTab={onOpenTab}
            />
            <StatCard title="Órdenes" value={count(d.kpis.orders)} hint={`Últimos ${d.days} días`} tab="orders" onOpenTab={onOpenTab} />
            <StatCard
              title="Por cobrar"
              value={money(receivable)}
              tone={receivable > 0 ? 'warn' : 'good'}
              hint={`${count(d.receivable.unpaid_settlements)} liquidaciones sin pago · ${count(d.receivable.pending_payments)} pagos pendientes`}
              tab="payments"
              onOpenTab={onOpenTab}
            />
          </Section>
          <Section>
            <StatCard
              title="Productos rechazados"
              value={count(d.rejected_products.count)}
              tone={d.rejected_products.count > 0 ? 'bad' : 'good'}
              hint={d.rejected_products.with_sales > 0 ? `${count(d.rejected_products.with_sales)} con ventas (${money(d.rejected_products.sales_usd)})` : 'Sin ventas afectadas'}
              tab="catalog"
              onOpenTab={onOpenTab}
            />
            <StatCard
              title="Paquetes atascados"
              value={count(d.stuck_packages.count)}
              tone={d.stuck_packages.count > 0 ? 'bad' : 'good'}
              hint={`Más de ${d.stuck_packages.stuck_days} días sin avanzar`}
              tab="packages"
              onOpenTab={onOpenTab}
            />
            <StatCard
              title="Pagos pendientes"
              value={count(d.payments.pending)}
              tone={d.payments.pending > 0 ? 'warn' : 'good'}
              hint={`${money(d.payments.pending_amount)} sin completar`}
              tab="payments"
              onOpenTab={onOpenTab}
            />
            <StatCard
              title="Pagos conciliados"
              value={rec ? `${count(rec.reconciled)} de ${count(rec.checkable)}` : '—'}
              tone={rec && rec.reconciled < rec.checkable ? 'warn' : 'good'}
              hint={rec ? `Diferencia ${money(rec.difference)} (tolerancia ${money(rec.tolerance_usd)})` : undefined}
              tab="payments"
              onOpenTab={onOpenTab}
            />
          </Section>

          <ChartCard
            title="Ventas y utilidad diaria"
            subtitle="TikTok Shop en el período elegido."
            csv={daily.map((r) => ({ Fecha: r.date, Ventas: r.ventas, Utilidad: r.utilidad }))}
            filename="tiktok-ventas-utilidad-diaria"
          >
            {series.error ? (
              <ErrorAlert message={series.error} />
            ) : daily.length === 0 ? (
              <EmptyChart>{series.loading ? 'Cargando…' : 'No hay ventas de TikTok Shop en este período.'}</EmptyChart>
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
            <ActionAlerts items={tiktokActions} loading={openActions.loading} error={openActions.error} onOpenTab={onOpenTab} />
          </div>
        </>
      )}
    </div>
  )
}
