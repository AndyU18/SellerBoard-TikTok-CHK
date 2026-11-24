import { ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'

import { api, CHANNEL_LABEL, type Filters } from '@/lib/api'
import { change, count, money, percent, PRESETS, previousRange } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type Tone = 'good' | 'bad' | 'plain'

function Card({
  title,
  value,
  tone = 'plain',
  hint,
  delta,
  goodWhenUp = true,
}: {
  title: string
  value: string
  tone?: Tone
  hint?: string
  delta?: number | null
  goodWhenUp?: boolean
}) {
  const color = tone === 'good' ? 'text-emerald-600' : tone === 'bad' ? 'text-red-600' : 'text-slate-900 dark:text-slate-100'
  const up = (delta ?? 0) >= 0
  return (
    <div className="rounded-lg border bg-white p-3 shadow-xs dark:bg-slate-900">
      <div className="text-muted-foreground text-[11px] font-semibold tracking-wide uppercase">{title}</div>
      <div className={`mt-1 text-xl font-bold tabular-nums ${color}`}>{value}</div>
      <div className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px]">
        {hint && <span>{hint}</span>}
        {delta != null && (
          <span className={up === goodWhenUp ? 'text-emerald-600' : 'text-red-600'} title="Contra el período anterior">
            {up ? '▲' : '▼'} {Math.abs(delta * 100).toFixed(1)}%
          </span>
        )}
      </div>
    </div>
  )
}

/** Texto de lo que se está viendo: "TikTok Shop · tiktok_shop_us · Últimos 90 días". */
export function useScopeDescription(): string {
  const scope = useScope()
  const channel = scope.channel === 'all' ? 'Todos los canales' : CHANNEL_LABEL[scope.channel]
  const store = scope.account ? scope.account : scope.channel === 'all' ? '' : 'todas las tiendas'
  const period = PRESETS.find((p) => p.value === scope.preset)?.label ?? ''
  return [channel, store, period].filter(Boolean).join(' · ')
}

/**
 * Encabezado de cada pantalla: título descriptivo según lo seleccionado y franja de indicadores clave
 * calculados con el mismo canal, tienda y período que los filtros de arriba.
 */
export function PageHeader({ title, showKpis = true }: { title: string; showKpis?: boolean }) {
  const scope = useScope()
  const description = useScopeDescription()
  const [open, setOpen] = useState(true)
  const filters: Filters = { ...scope.range, channel: scope.channelParam, account: scope.account }
  const prev = previousRange(scope.preset)

  const cur = useAsync(() => (showKpis ? api.profit('channel', filters) : Promise.resolve(null)), [
    showKpis,
    scope.range.start,
    scope.range.end,
    scope.channelParam,
    scope.account,
  ])
  const before = useAsync(
    () => (showKpis && prev ? api.profit('channel', { ...filters, start: prev.start, end: prev.end }) : Promise.resolve(null)),
    [showKpis, prev?.start, prev?.end, scope.channelParam, scope.account],
  )
  const pending = useAsync(() => (showKpis ? api.cashflow(filters) : Promise.resolve(null)), [
    showKpis,
    scope.range.start,
    scope.range.end,
    scope.channelParam,
    scope.account,
  ])

  const t = cur.data?.totals
  const p = before.data?.totals
  const net = t ? t.gross_sales - t.promo : 0
  const d = (key: 'gross_sales' | 'units' | 'refund_amount' | 'ad_spend' | 'net_profit' | 'shipping_cost' | 'platform_fees' | 'cogs') =>
    t && p ? change(t[key], p[key]) : null
  const contribution = t ? net - t.platform_fees - t.shipping_cost - t.refund_amount - t.cogs : 0

  return (
    <section aria-label="Resumen de la vista" className="space-y-3 border-b bg-slate-50 px-6 py-4 dark:bg-slate-950">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          {title} <span className="text-muted-foreground font-normal">— {description}</span>
        </h2>
        {showKpis && (
          <button type="button" onClick={() => setOpen(!open)} className="text-muted-foreground flex items-center gap-1 text-xs hover:underline">
            {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
            {open ? 'Ocultar indicadores' : 'Mostrar indicadores'}
          </button>
        )}
      </div>
      {showKpis && open && (
        <>
          {cur.error && <div className="text-xs text-red-700">No se pudieron cargar los indicadores: {cur.error}</div>}
          {cur.loading && !t && <div className="text-muted-foreground text-xs">Calculando indicadores…</div>}
          {t && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
              <Card title="Ventas netas" value={money(net)} delta={t && p ? change(net, p.gross_sales - p.promo) : null} hint={`Bruto ${money(t.gross_sales)} · Promo ${money(t.promo)}`} />
              <Card title="Unidades" value={`${count(t.units)} u.`} delta={d('units')} hint={`Ticket prom. ${t.units ? money(net / t.units) : '—'} / u.`} />
              <Card title="Reembolsos" value={money(-t.refund_amount)} tone="bad" delta={d('refund_amount')} goodWhenUp={false} hint={`${t.gross_sales ? percent(t.refund_amount / t.gross_sales) : '—'} de las ventas`} />
              <Card title="Publicidad" value={money(-t.ad_spend)} tone="bad" delta={d('ad_spend')} goodWhenUp={false} hint={`TACOS ${t.tacos == null ? '—' : percent(t.tacos)}`} />
              <Card title="Ganancia neta" value={money(t.net_profit)} tone={t.net_profit >= 0 ? 'good' : 'bad'} delta={d('net_profit')} hint={`Margen ${t.margin == null ? '—' : percent(t.margin)} · ROI ${t.roi == null ? '—' : percent(t.roi)}`} />
              <Card title="Costos de envío" value={money(-t.shipping_cost)} tone="bad" delta={d('shipping_cost')} goodWhenUp={false} hint={`${t.units ? money(t.shipping_cost / t.units) : '—'} por unidad`} />
              <Card title="Tarifas de la plataforma" value={money(-t.platform_fees)} tone="bad" delta={d('platform_fees')} goodWhenUp={false} hint={net ? `${percent(t.platform_fees / net)} de las ventas netas` : undefined} />
              <Card title="Costo de mercancía (COGS)" value={money(-t.cogs)} tone="bad" delta={d('cogs')} goodWhenUp={false} hint="Costo por SKU" />
              <Card title="Margen de contribución" value={money(contribution)} tone={contribution >= 0 ? 'good' : 'bad'} hint={`Margen bruto ${net ? percent(contribution / net) : '—'}`} />
              <Card title="Por liquidar" value={pending.data ? money(pending.data.summary.pending) : '—'} hint={pending.data ? `Cobrado ${money(pending.data.summary.received)}` : 'Depósitos proyectados'} />
            </div>
          )}
        </>
      )}
    </section>
  )
}
