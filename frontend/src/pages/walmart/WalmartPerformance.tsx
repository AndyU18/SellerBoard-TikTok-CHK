// "Desempeño del vendedor" de Walmart: indicadores actuales contra su umbral + el componente con historial.
import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, PILL, Pill, Segmented } from '@/components/analytics/ui'
import { useScopeDescription } from '@/components/PageHeader'
import { SellerPerformance } from '@/components/SellerPerformance'
import { Card, CardContent } from '@/components/ui/card'
import { api, type Filters, type PerformanceWindow } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { PageTitle } from '@/pages/walmart/walmartUi'

type MetricKey = keyof Omit<PerformanceWindow, 'window_days'>
type Light = 'ok' | 'near' | 'bad' | 'info'

interface Indicator {
  key: MetricKey
  label: string
  explain: string
  /** 'min': debe estar sobre la meta; 'max': debe estar bajo la meta; sin meta: solo informativo. */
  goal?: { kind: 'min' | 'max'; target: string }
}

const DEFAULT_WINDOW_DAYS = 30
/** Puntos porcentuales de margen antes de que un indicador pase de ámbar a rojo. */
const NEAR_MARGIN_POINTS = 2

const INDICATORS: Indicator[] = [
  {
    key: 'ots',
    label: 'Enviado a tiempo (OTS)',
    explain: 'Porcentaje de pedidos que enviaste antes del plazo de envío que prometió Walmart.',
    goal: { kind: 'min', target: 'ots' },
  },
  {
    key: 'otd',
    label: 'Entregado a tiempo (OTD)',
    explain: 'Porcentaje de pedidos que llegaron al cliente dentro de la fecha de entrega prometida.',
    goal: { kind: 'min', target: 'otd' },
  },
  {
    key: 'vtr',
    label: 'Guía válida (VTR)',
    explain: 'Porcentaje de envíos con un número de rastreo válido que el transportista sí reconoce.',
    goal: { kind: 'min', target: 'vtr' },
  },
  {
    key: 'cancellation_seller_fault',
    label: 'Cancelaciones (tu culpa)',
    explain: 'Pedidos que cancelaste tú, por ejemplo por falta de stock. Mientras más bajo, mejor.',
    goal: { kind: 'max', target: 'cancellation_seller_fault' },
  },
  {
    key: 'refund_rate_seller_fault',
    label: 'Reembolsos (tu culpa)',
    explain: 'Reembolsos causados por tu envío o producto (dañado, equivocado). Informativo.',
  },
  {
    key: 'cancellation_customer_fault',
    label: 'Cancelaciones (cliente)',
    explain: 'Pedidos cancelados por el cliente; normalmente no afectan tu calificación. Informativo.',
  },
  {
    key: 'refund_rate_customer_fault',
    label: 'Reembolsos (cliente)',
    explain: 'Reembolsos por motivos del cliente, como cambio de opinión. Informativo.',
  },
]

const LIGHT: Record<Light, { label: string; pill: string; border: string }> = {
  ok: { label: 'En meta', pill: PILL.green, border: 'border-l-emerald-500' },
  near: { label: 'Cerca del límite', pill: PILL.amber, border: 'border-l-amber-500' },
  bad: { label: 'Fuera de meta', pill: PILL.red, border: 'border-l-red-500' },
  info: { label: 'Informativo', pill: PILL.slate, border: 'border-l-slate-300' },
}

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`)

function traffic(indicator: Indicator, value: number | null, targets: Record<string, number>): Light {
  const target = indicator.goal ? targets[indicator.goal.target] : undefined
  if (value == null || !indicator.goal || target == null) return 'info'
  if (indicator.goal.kind === 'min') {
    if (value >= target) return 'ok'
    return value >= target - NEAR_MARGIN_POINTS ? 'near' : 'bad'
  }
  if (value <= target) return 'ok'
  return value <= target + NEAR_MARGIN_POINTS ? 'near' : 'bad'
}

function LightCard({ indicator, value, targets }: { indicator: Indicator; value: number | null; targets: Record<string, number> }) {
  const light = traffic(indicator, value, targets)
  const style = LIGHT[light]
  const target = indicator.goal ? targets[indicator.goal.target] : undefined
  return (
    <Card className={`border-l-4 ${style.border}`}>
      <CardContent className="space-y-1 pt-4">
        <div className="flex items-start justify-between gap-2">
          <span className="text-muted-foreground text-xs font-medium">{indicator.label}</span>
          <Pill label={style.label} className={style.pill} />
        </div>
        <div className="text-2xl font-semibold tabular-nums">{pct(value)}</div>
        {indicator.goal && target != null && (
          <div className="text-muted-foreground text-xs">
            Meta: {indicator.goal.kind === 'min' ? 'al menos' : 'máximo'} {pct(target)}
          </div>
        )}
        <p className="text-muted-foreground text-xs">{indicator.explain}</p>
      </CardContent>
    </Card>
  )
}

export function WalmartPerformance() {
  const scope = useScope()
  const description = useScopeDescription()
  const filters = useMemo<Filters>(() => ({ channel: scope.channelParam, account: scope.account }), [scope.channelParam, scope.account])
  const data = useAsync(() => api.sellerPerformance(filters), [filters])
  const [picked, setPicked] = useState<number | null>(null)

  const targets = data.data?.targets ?? {}
  const accounts = useMemo(() => data.data?.accounts ?? [], [data.data])
  const windowDays = useMemo(() => [...new Set(accounts.flatMap((a) => a.windows.map((w) => w.window_days)))].sort((a, b) => a - b), [accounts])
  const activeWindow = picked != null && windowDays.includes(picked) ? picked : windowDays.includes(DEFAULT_WINDOW_DAYS) ? DEFAULT_WINDOW_DAYS : windowDays[0]

  return (
    <div className="space-y-6">
      <PageTitle
        title="Desempeño del vendedor"
        scope={description}
        note="Indicadores que Walmart usa para calificar tu cuenta. El semáforo compara cada uno con su umbral de referencia."
      />
      <ErrorAlert message={data.error} />
      {data.loading && !data.data && <p className="text-muted-foreground text-sm">Cargando…</p>}
      {!data.loading && !data.error && accounts.length === 0 && (
        <Callout tone="info">
          Todavía no hay una foto del desempeño de Walmart. Se guarda en cada sincronización de la cuenta.
        </Callout>
      )}

      {windowDays.length > 1 && (
        <Segmented
          label="Ventana de tiempo"
          value={String(activeWindow)}
          options={windowDays.map((d) => ({ value: String(d), label: `${d} días` }))}
          onChange={(v) => setPicked(Number(v))}
        />
      )}

      {accounts.map((a) => {
        const win = a.windows.find((w) => w.window_days === activeWindow)
        return (
          <section key={a.account} className="space-y-3" aria-label={`Indicadores de ${a.account}`}>
            <h2 className="text-base font-semibold">
              {a.account}
              <span className="text-muted-foreground ml-2 text-xs font-normal">al {a.snapshot_date}</span>
            </h2>
            {win ? (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {INDICATORS.map((i) => (
                  <LightCard key={i.key} indicator={i} value={win[i.key]} targets={targets} />
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">Esta cuenta no tiene datos para {activeWindow} días.</p>
            )}
          </section>
        )
      })}

      <SellerPerformance />
    </div>
  )
}
