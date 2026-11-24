import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { CashProjection } from '@/components/analytics/CashProjection'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorAlert } from '@/components/analytics/ui'
import { ChartCard, EmptyChart } from '@/components/charts/kit'
import { ComboChart } from '@/components/charts/ComboChart'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { api, type Filters } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { shortDate, weekKey } from '@/lib/chartdata_dinero'
import { count, money, tone } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const STATUS: Record<string, { label: string; badge: string }> = {
  paid: { label: 'Cobrado', badge: 'bg-green-100 text-green-800' },
  processing: { label: 'Por cobrar', badge: 'bg-amber-100 text-amber-800' },
  failed: { label: 'Fallido', badge: 'bg-red-100 text-red-800' },
}

function Kpi({ title, value, hint, className = '' }: { title: string; value: string; hint?: string; className?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold ${className}`}>{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

export function Cashflow() {
  const scope = useScope()
  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.cashflow(filters), [filters])
  const [week, setWeek] = useState<string | null>(null)

  const summary = data.data?.summary
  const allRows = useMemo(() => data.data?.rows ?? [], [data.data])
  const rows = week ? allRows.filter((r) => weekKey(r.payout_date ?? r.statement_date ?? '') === week) : allRows

  // Semanas por fecha de pago (o de cierre si aún no hay pago): entradas = ventas liquidadas, salidas = lo que descuenta la plataforma.
  const weekly = useMemo(() => {
    const m = new Map<string, { week: string; inflow: number; outflow: number; net: number; statements: number }>()
    for (const r of allRows) {
      const day = r.payout_date ?? r.statement_date
      if (!day) continue
      const k = weekKey(day)
      const w = m.get(k) ?? { week: k, inflow: 0, outflow: 0, net: 0, statements: 0 }
      w.inflow += r.gross_amount + Math.max(0, r.adjustment_amount)
      w.outflow -= r.fees_amount + r.shipping_amount + Math.max(0, -r.adjustment_amount)
      w.net += r.net_payout
      w.statements += 1
      m.set(k, w)
    }
    return [...m.values()].sort((a, b) => a.week.localeCompare(b.week))
  }, [allRows])

  const nextPayout = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10)
    return allRows
      .filter((r) => r.status === 'processing' && r.payout_date && r.payout_date >= today)
      .map((r) => r.payout_date as string)
      .sort()[0]
  }, [allRows])

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Flujo de caja y liquidaciones</h1>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="Las liquidaciones" />

      <ErrorAlert message={data.error} />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Cobrado" value={money(summary?.received ?? 0)} hint="ya depositado en tu banco" className="text-green-700" />
        <Kpi title="Por cobrar" value={money(summary?.pending ?? 0)} hint="liquidado, pago en proceso" />
        <Kpi
          title="Pagos fallidos"
          value={money(summary?.failed ?? 0)}
          hint="revisa la cuenta bancaria en la plataforma"
          className={(summary?.failed ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi title="Estados de cuenta" value={count(summary?.statements ?? 0)} hint="en el periodo" />
      </div>

      <CashProjection payoutDate={nextPayout} />

      <ChartCard
        title="Entradas y salidas por semana"
        subtitle="Por fecha de pago. Entradas = ventas liquidadas; salidas = comisiones, envío y ajustes negativos; línea = neto depositado. Clic en una semana para filtrar la tabla."
        csv={weekly.map((w) => ({ semana: w.week, entradas: w.inflow, salidas: w.outflow, neto: w.net, estados_de_cuenta: w.statements }))}
        filename="flujo-de-caja-semanal"
        actions={
          week ? (
            <button type="button" onClick={() => setWeek(null)} className="rounded border px-2 py-0.5 text-xs hover:bg-slate-50">
              Semana {shortDate(week)} · quitar
            </button>
          ) : null
        }
      >
        {weekly.length === 0 ? (
          <EmptyChart>{data.loading ? 'Cargando…' : 'Sin liquidaciones. Sincroniza TikTok Shop desde Importar.'}</EmptyChart>
        ) : (
          <ComboChart
            data={weekly}
            xKey="week"
            xFormat={(v) => shortDate(v)}
            onPointClick={(r) => setWeek(String(r.week))}
            series={[
              { key: 'inflow', label: 'Entradas (ventas liquidadas)', type: 'bar', color: '#10b981', stack: 'f' },
              { key: 'outflow', label: 'Salidas (comisiones, envío, ajustes)', type: 'bar', color: '#ef4444', stack: 'f' },
              { key: 'net', label: 'Neto depositado', type: 'line', color: '#3b82f6' },
            ]}
          />
        )}
      </ChartCard>

      <ChartCard
        title="Cobrado y por cobrar por mes"
        csv={(data.data?.by_month ?? []).map((m) => ({ mes: m.month, cobrado: m.received, por_cobrar: m.pending }))}
        filename="cobrado-por-mes"
      >
        {(data.data?.by_month.length ?? 0) === 0 ? (
          <EmptyChart>{data.loading ? 'Cargando…' : 'Sin liquidaciones. Sincroniza TikTok Shop desde Importar.'}</EmptyChart>
        ) : (
          <ComboChart
            data={data.data?.by_month ?? []}
            xKey="month"
            height={280}
            series={[
              { key: 'received', label: 'Cobrado', type: 'bar', color: '#16a34a', stack: 'c' },
              { key: 'pending', label: 'Por cobrar', type: 'bar', color: '#f59e0b', stack: 'c' },
            ]}
          />
        )}
      </ChartCard>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cierre</TableHead>
                <TableHead>Pago</TableHead>
                <TableHead>Canal</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Ingresos</TableHead>
                <TableHead className="text-right">Comisiones</TableHead>
                <TableHead className="text-right">Envío</TableHead>
                <TableHead className="text-right">Ajustes</TableHead>
                <TableHead className="text-right">Neto a depositar</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin liquidaciones en este periodo.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => {
                const st = STATUS[r.status ?? ''] ?? { label: r.status ?? '—', badge: 'bg-slate-100 text-slate-700' }
                return (
                  <TableRow key={`${r.channel}-${r.external_id}`}>
                    <TableCell>{r.statement_date ?? '—'}</TableCell>
                    <TableCell>{r.payout_date ?? '—'}</TableCell>
                    <TableCell>
                      <ChannelBadge channel={r.channel} />
                    </TableCell>
                    <TableCell>
                      <span className={`rounded px-2 py-0.5 text-xs ${st.badge}`}>{st.label}</span>
                    </TableCell>
                    <TableCell className="text-right">{money(r.gross_amount)}</TableCell>
                    <TableCell className="text-right">{money(r.fees_amount)}</TableCell>
                    <TableCell className="text-right">{money(r.shipping_amount)}</TableCell>
                    <TableCell className={`text-right ${tone(r.adjustment_amount)}`}>{money(r.adjustment_amount)}</TableCell>
                    <TableCell className="text-right font-medium">{money(r.net_payout)}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <p className="text-muted-foreground max-w-3xl text-xs">
        Por ahora solo TikTok Shop informa sus estados de cuenta. Amazon, Walmart y Shopify aparecerán aquí cuando
        tengan conector de liquidaciones. Los gastos de Redo (etiquetas) se pagan por separado y no están incluidos.
      </p>
    </div>
  )
}
