import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { settlementAging, type AgingBucket, type AgingOrder, type SettlementDifference } from '@/lib/settlementAging'
import { useAsync } from '@/lib/useAsync'

/** Cuántas de las órdenes más viejas se piden al servidor (la tabla y su CSV trabajan sobre ellas). */
const OLDEST_LIMIT = 200
/** Días de antigüedad desde los que se considera «mucho tiempo» (rojo): el tope de la cubeta anterior a la última. */
const SEVERE_FROM_DAYS = 31

/** Color de la barra según el atraso: en plazo, tarde, muy tarde. */
function bucketColor(b: AgingBucket): string {
  if (!b.overdue) return COLOR.good
  return b.min_days >= SEVERE_FROM_DAYS ? COLOR.bad : COLOR.amber
}

const ORIGIN_COLOR = { tiktok_shop: '#3b82f6', affiliate: '#8b5cf6' } as const
const SERIES: ComboSeries[] = [
  { key: 'tiktok_shop_usd', label: 'TikTok Shop', type: 'bar', stack: 'origin', color: ORIGIN_COLOR.tiktok_shop, format: money },
  { key: 'affiliate_usd', label: 'Afiliados', type: 'bar', stack: 'origin', color: ORIGIN_COLOR.affiliate, format: money },
]

/** Barras horizontales por cubeta de antigüedad. Clic filtra la tabla; otro clic en la misma la quita. */
function BucketBars({
  buckets,
  selected,
  onSelect,
}: {
  buckets: AgingBucket[]
  selected: string | null
  onSelect: (key: string | null) => void
}) {
  const top = Math.max(...buckets.map((b) => b.pending_usd), 0)
  return (
    <div className="space-y-2">
      {buckets.map((b) => {
        const active = selected === b.key
        return (
          <button
            key={b.key}
            type="button"
            aria-pressed={active}
            onClick={() => onSelect(active ? null : b.key)}
            className={`grid w-full grid-cols-[4.5rem_1fr] items-center gap-3 rounded p-1 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800 ${active ? 'bg-slate-100 ring-1 ring-slate-400 dark:bg-slate-800' : ''}`}
            title={`${b.label} días: ${count(b.orders)} órdenes, ${money(b.pending_usd)}`}
          >
            <span className="font-medium tabular-nums">{b.label} d</span>
            <span className="relative flex h-8 items-center rounded bg-slate-100 dark:bg-slate-800">
              {b.pending_usd > 0 && (
                <span
                  className="absolute inset-y-0 left-0 rounded"
                  style={{ width: `${Math.max(2, (b.pending_usd / top) * 100)}%`, background: bucketColor(b), opacity: 0.85 }}
                />
              )}
              <span className="relative flex w-full justify-between gap-2 px-3 text-xs font-medium">
                <span>{count(b.orders)} órdenes</span>
                <span className="tabular-nums">{money(b.pending_usd)}</span>
              </span>
            </span>
          </button>
        )
      })}
      <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 pt-1 text-[11px]">
        <Legend color={COLOR.good} label="En plazo" />
        <Legend color={COLOR.amber} label="Atrasada" />
        <Legend color={COLOR.bad} label="Más de un mes" />
      </div>
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="inline-block size-2.5 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  )
}

function OrdersTable({ rows }: { rows: AgingOrder[] }) {
  return (
    <div className="max-h-96 overflow-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Orden</TableHead>
            <TableHead>Origen</TableHead>
            <TableHead>Desde</TableHead>
            <TableHead className="text-right">Días</TableHead>
            <TableHead className="text-right">Monto estimado</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((o) => (
            <TableRow key={`${o.origin}-${o.order_id}`}>
              <TableCell className="font-mono text-xs">{o.order_id}</TableCell>
              <TableCell>
                <Pill label={o.origin_label} className={o.origin === 'affiliate' ? PILL.blue : PILL.slate} />
                {o.creator && <span className="text-muted-foreground ml-2 text-xs">{o.creator}</span>}
              </TableCell>
              <TableCell className="text-xs whitespace-nowrap">
                {o.anchor_date} <span className="text-muted-foreground">({o.anchor_kind})</span>
              </TableCell>
              <TableCell className="text-right">
                <Pill label={`${o.age_days} d`} className={!o.overdue ? PILL.green : o.age_days >= SEVERE_FROM_DAYS ? PILL.red : PILL.amber} />
              </TableCell>
              <TableCell className="text-right tabular-nums">{money(o.estimated_usd)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function DifferencesTable({ rows }: { rows: SettlementDifference[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Liquidación</TableHead>
            <TableHead>Fecha</TableHead>
            <TableHead className="text-right">Depósito</TableHead>
            <TableHead className="text-right">Suma de transacciones</TableHead>
            <TableHead className="text-right">Diferencia</TableHead>
            <TableHead>Conceptos</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((d) => (
            <TableRow key={d.settlement_id}>
              <TableCell className="font-mono text-xs">{d.external_id}</TableCell>
              <TableCell className="text-xs whitespace-nowrap">{d.statement_date ?? '—'}</TableCell>
              <TableCell className="text-right tabular-nums">{money(d.deposit_usd)}</TableCell>
              <TableCell className="text-right tabular-nums">{money(d.transactions_usd)}</TableCell>
              <TableCell className={`text-right font-medium tabular-nums ${d.difference_usd < 0 ? 'text-red-600' : 'text-emerald-600'}`}>{money(d.difference_usd)}</TableCell>
              <TableCell className="text-muted-foreground max-w-xs text-xs">
                {d.concepts.length === 0
                  ? `Sin transacciones: TikTok solo trae un ajuste global de ${money(d.adjustment_usd)}, sin concepto.`
                  : d.concepts.map((c) => `${c.concept} ${money(c.amount_usd)}`).join(' · ')}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function SettlementAging() {
  const { channel } = useScope()
  const description = useScopeDescription()
  const applies = channel === 'all' || channel === 'tiktok_shop'
  const data = useAsync(() => (applies ? settlementAging.get({ limit: OLDEST_LIMIT }) : Promise.resolve(null)), [applies])
  const d = data.data
  const [bucket, setBucket] = useState<string | null>(null)

  const buckets = useMemo(() => d?.buckets ?? [], [d?.buckets])
  const picked = buckets.find((b) => b.key === bucket) ?? null
  const orders = useMemo(() => (d?.oldest_orders ?? []).filter((o) => !picked || o.bucket === picked.label), [d?.oldest_orders, picked])
  const empty = (msg: string) => <EmptyChart>{data.loading ? 'Cargando…' : msg}</EmptyChart>

  const t = d?.totals
  const a = d?.assumptions
  const selectByLabel = (row: Record<string, string | number | null | undefined>) => setBucket((cur) => (cur === row.key ? null : String(row.key)))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dinero atorado en liquidaciones</h1>
        <p className="text-muted-foreground text-xs">
          {description}
          {d && ` · al ${d.as_of} · plazo normal de liquidación: ${d.expected_days} días desde la entrega`}
        </p>
      </div>

      {!applies && <Callout tone="info">Las liquidaciones solo existen para TikTok Shop. Elige «Todos los canales» o TikTok Shop en el filtro de arriba.</Callout>}
      <ErrorAlert message={data.error} />
      {applies && data.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      {t && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Kpi title="Dinero pendiente" value={money(t.pending_usd)} hint={`${count(t.orders)} órdenes sin liquidar (estimado)`} />
          <Kpi
            title="Atrasado"
            value={money(t.overdue_usd)}
            hint={`${count(t.overdue_orders)} órdenes con más de ${d?.expected_days} días`}
            className={t.overdue_usd > 0 ? 'text-red-600' : ''}
          />
          <Kpi title="Orden más vieja" value={t.orders ? `${count(t.oldest_days)} días` : '—'} hint="Desde su entrega o envío" />
          <Kpi
            title="Liquidaciones con diferencia"
            value={count(t.difference_count)}
            hint={`${money(t.difference_usd)} entre depósito y transacciones`}
            className={t.difference_count > 0 ? 'text-red-600' : ''}
          />
        </div>
      )}

      {d && (
        <div className="grid gap-4 xl:grid-cols-2">
          <ChartCard
            title="Antigüedad del dinero pendiente"
            subtitle="Clic en una barra para filtrar la tabla de órdenes; otro clic la quita."
            filename="liquidaciones-antiguedad"
            csv={buckets.map((b) => ({ Dias: b.label, Atrasada: b.overdue ? 'si' : 'no', Ordenes: b.orders, 'Pendiente USD': b.pending_usd }))}
          >
            {t?.orders ? <BucketBars buckets={buckets} selected={bucket} onSelect={setBucket} /> : empty('No hay órdenes pendientes de liquidar.')}
          </ChartCard>

          <ChartCard
            title="Pendiente por canal"
            subtitle="Barras apiladas por antigüedad: TikTok Shop contra ventas de afiliados. Clic en una barra para filtrar."
            filename="liquidaciones-por-canal"
            csv={buckets.map((b) => ({ Dias: b.label, 'TikTok Shop USD': b.tiktok_shop_usd, 'Afiliados USD': b.affiliate_usd, 'Ordenes TikTok': b.tiktok_shop_orders, 'Ordenes afiliados': b.affiliate_orders }))}
          >
            {t?.orders ? (
              <ComboChart data={buckets.map((b) => ({ key: b.key, label: b.label, tiktok_shop_usd: b.tiktok_shop_usd, affiliate_usd: b.affiliate_usd }))} xKey="label" xFormat={(v) => `${v} d`} series={SERIES} height={260} onPointClick={selectByLabel} />
            ) : (
              empty('No hay órdenes pendientes de liquidar.')
            )}
          </ChartCard>
        </div>
      )}

      {d && (
        <ChartCard
          title={picked ? `Órdenes más viejas · ${picked.label} días` : 'Órdenes más viejas sin liquidar'}
          subtitle={`${count(orders.length)} de las ${count(d.oldest_orders.length)} más viejas. El monto es un estimado.`}
          filename="liquidaciones-ordenes-viejas"
          csv={orders.map((o) => ({ Orden: o.order_id, Origen: o.origin_label, Creador: o.creator, Desde: o.anchor_date, 'Tipo de fecha': o.anchor_kind, Dias: o.age_days, 'Estimado USD': o.estimated_usd }))}
          actions={
            picked && (
              <button type="button" onClick={() => setBucket(null)} className="text-muted-foreground hover:text-foreground rounded border px-2 py-1 text-xs">
                Quitar filtro
              </button>
            )
          }
        >
          {orders.length === 0 ? empty(picked ? 'Ninguna de las órdenes más viejas cae en esta cubeta.' : 'No hay órdenes pendientes de liquidar.') : <OrdersTable rows={orders} />}
        </ChartCard>
      )}

      {d && (
        <ChartCard
          title="Liquidaciones con diferencia"
          subtitle="El depósito no coincide con la suma de sus transacciones (sin conceptos informativos)."
          filename="liquidaciones-diferencias"
          csv={d.differences.map((x) => ({ Liquidacion: x.external_id, Fecha: x.statement_date, Deposito: x.deposit_usd, Transacciones: x.transactions_usd, Diferencia: x.difference_usd, 'Ajuste global': x.adjustment_usd }))}
        >
          {d.differences.length === 0 ? empty('Todos los depósitos cuadran con sus transacciones.') : <DifferencesTable rows={d.differences} />}
        </ChartCard>
      )}

      {a && (
        <Hint>
          Las órdenes sin liquidar no traen monto en TikTok: se estiman con el ticket promedio de las liquidadas ({money(a.avg_order_usd)}, {count(a.sample_orders)} órdenes)
          menos la comisión efectiva histórica ({a.fee_rate == null ? '—' : `${(a.fee_rate * 100).toFixed(1)}%`}); las de afiliados usan su base de comisión. Una orden de afiliado
          no se cuenta también como de TikTok Shop. Las muestras no se incluyen. La pantalla muestra el estado actual: no usa el rango de fechas.
        </Hint>
      )}
    </div>
  )
}
