import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money, tone } from '@/lib/format'
import { redo } from '@/lib/redo'
import { useAsync } from '@/lib/useAsync'

const day = (s: string | null | undefined) => (s ? s.slice(0, 10) : '—')

/** Barra de progreso simple (el valor puede pasar de 100 y se recorta al dibujar). */
function Progress({ label, value, text, warn }: { label: string; value: number | null; text: string; warn?: boolean }) {
  return (
    <div>
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium">
          {value != null ? `${value.toFixed(0)}% · ` : ''}
          {text}
        </span>
      </div>
      <div className="h-2.5 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-label={label} aria-valuenow={value == null ? undefined : Math.min(100, Math.round(value))} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full rounded-full ${warn ? 'bg-amber-500' : 'bg-blue-600'}`} style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
      </div>
    </div>
  )
}

function billStatus(status: string | null): { label: string; pill: string } {
  const s = (status ?? '').toUpperCase()
  if (s === 'PAID' || s === 'SUCCESS' || s === 'CLOSED') return { label: 'Pagada', pill: PILL.green }
  if (s === 'OPEN' || s === 'PENDING') return { label: 'Abierta', pill: PILL.amber }
  if (s === 'FAILED') return { label: 'Fallida', pill: PILL.red }
  return { label: status ?? '—', pill: PILL.slate }
}

export function RedoBilling() {
  const data = useAsync(() => redo.billing(), [])
  const d = data.data
  const t = d?.topups
  const burn = d?.burn
  const [now] = useState(() => Date.now())
  const empty = (msg: string) => <EmptyChart>{data.loading ? 'Cargando…' : msg}</EmptyChart>
  const months = useMemo(() => (d?.by_month ?? []).map((m) => ({ month: m.month, charges: m.charges, topups: m.topups, gap: m.gap })), [d?.by_month])
  const cumulative = useMemo(() => {
    const out: { month: string; charges: number; topups: number; balance: number }[] = []
    for (const m of months) {
      const prev = out[out.length - 1]
      const c = (prev?.charges ?? 0) + m.charges
      const tp = (prev?.topups ?? 0) + m.topups
      out.push({ month: m.month, charges: Math.round(c * 100) / 100, topups: Math.round(tp * 100) / 100, balance: Math.round((tp - c) * 100) / 100 })
    }
    return out
  }, [months])
  const recent = useMemo(
    () =>
      [...(t?.recent ?? [])].reverse().map((r) => {
        const ok = (r.status ?? '').toUpperCase() === 'SUCCESS'
        return { date: day(r.created_at), status: r.status ?? '', amount: r.amount, ok: ok ? r.amount : null, failed: ok ? null : r.amount }
      }),
    [t?.recent],
  )
  // Días transcurridos desde la última recarga frente a lo que dura una recarga promedio.
  const elapsedDays = t?.last_at ? Math.max(0, (now - new Date(t.last_at).getTime()) / 86_400_000) : null
  const elapsedPct = elapsedDays != null && burn?.days_per_topup ? (elapsedDays / burn.days_per_topup) * 100 : null
  const coverPct = burn?.days_per_topup != null && t?.avg_days_between ? (burn.days_per_topup / t.avg_days_between) * 100 : null

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Redo: facturación y saldo</h1>
      <ErrorAlert message={data.error} />
      {data.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      <Callout tone="info">
        Las recargas de saldo <strong>no son un costo</strong>: es dinero que adelantas a Redo y que se va consumiendo
        con las etiquetas. El costo real son los cargos. Aquí puedes ver si el saldo alcanza y cada cuánto recargas.
      </Callout>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Kpi title="Total recargado" value={money(t?.total ?? 0)} hint={`${count(t?.count ?? 0)} recargas exitosas`} />
        <Kpi title="Recarga promedio" value={money(t?.average)} hint="monto por recarga" />
        <Kpi
          title="Cada cuántos días recargas"
          value={t?.avg_days_between != null ? `${t.avg_days_between.toFixed(1)} d` : '—'}
          hint="promedio entre recargas"
        />
        <Kpi
          title="Última recarga"
          value={money(t?.last_amount)}
          hint={t?.last_at ? `el ${day(t.last_at)}` : 'sin recargas'}
        />
        <Kpi
          title="Recargas fallidas"
          value={count(t?.failed ?? 0)}
          hint="intentos que no se cobraron"
          className={(t?.failed ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi title="Comisión de tarjeta acumulada" value={money(t?.card_fees ?? 0)} hint="lo que cobra la tarjeta por recargar" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ritmo de gasto</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <div className="text-muted-foreground text-xs">Gasto en 30 días</div>
              <div className="text-2xl font-semibold">{money(burn?.spend_30d ?? 0)}</div>
            </div>
            <div>
              <div className="text-muted-foreground text-xs">Gasto diario</div>
              <div className="text-2xl font-semibold">{money(burn?.daily ?? 0)}</div>
            </div>
            <div>
              <div className="text-muted-foreground text-xs">Una recarga promedio dura</div>
              <div className="text-2xl font-semibold">
                {burn?.days_per_topup != null ? `${burn.days_per_topup.toFixed(1)} días` : '—'}
              </div>
            </div>
          </div>
          <div className="space-y-3">
            <Progress
              label="Cuánto de la recarga promedio ya se gastó desde la última recarga"
              value={elapsedPct}
              text={
                elapsedDays != null && burn?.days_per_topup != null
                  ? `${elapsedDays.toFixed(1)} de ${burn.days_per_topup.toFixed(1)} días`
                  : 'sin datos suficientes'
              }
              warn={elapsedPct != null && elapsedPct >= 80}
            />
            <Progress
              label="Duración de una recarga frente a cada cuánto recargas"
              value={coverPct}
              text={
                burn?.days_per_topup != null && t?.avg_days_between != null
                  ? `dura ${burn.days_per_topup.toFixed(1)} d · recargas cada ${t.avg_days_between.toFixed(1)} d`
                  : 'sin datos suficientes'
              }
              warn={coverPct != null && coverPct < 100}
            />
          </div>
          <Hint>
            Barra completa en la primera: la recarga promedio ya se debería haber agotado. En la segunda, menos de 100%
            significa que recargas antes de que se acabe el saldo estimado (con margen); más de 100%, que el saldo dura
            más que el intervalo entre recargas.
          </Hint>
        </CardContent>
      </Card>

      <ChartCard
        title="Cargos y recargas por mes"
        subtitle="Barras: cargos (costo) y recargas (financiamiento). Línea: brecha del mes (cargos − recargas); sobre cero el saldo bajó."
        filename="redo-cargos-y-recargas"
        csv={months.map((m) => ({ Mes: m.month, Cargos: m.charges, Recargas: m.topups, Brecha: m.gap }))}
      >
        {months.length === 0 ? (
          empty('Sin movimientos todavía.')
        ) : (
          <ComboChart
            data={months}
            xKey="month"
            series={[
              { key: 'charges', label: 'Cargos (costo)', type: 'bar', color: '#dc2626', format: money },
              { key: 'topups', label: 'Recargas (financiamiento)', type: 'bar', color: '#2563eb', format: money },
              { key: 'gap', label: 'Brecha (cargos − recargas)', type: 'line', color: '#0f172a', format: money },
            ]}
          />
        )}
      </ChartCard>

      <ChartCard
        title="Acumulado: cargos frente a recargas"
        subtitle="Si la línea de recargas va por encima de la de cargos, el saldo por adelantado crece; la zona entre ambas es el saldo estimado."
        filename="redo-acumulado"
        csv={cumulative.map((m) => ({ Mes: m.month, 'Cargos acumulados': m.charges, 'Recargas acumuladas': m.topups, 'Saldo estimado': m.balance }))}
      >
        {cumulative.length === 0 ? (
          empty('Sin movimientos todavía.')
        ) : (
          <ComboChart
            data={cumulative}
            xKey="month"
            series={[
              { key: 'topups', label: 'Recargas acumuladas', type: 'area', color: '#2563eb', format: money },
              { key: 'charges', label: 'Cargos acumulados', type: 'line', color: '#dc2626', format: money },
              { key: 'balance', label: 'Saldo estimado (recargas − cargos)', type: 'line', color: '#16a34a', format: money },
            ]}
          />
        )}
        {months.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Mes</TableHead>
                  <TableHead className="text-right">Cargos</TableHead>
                  <TableHead className="text-right">Recargas</TableHead>
                  <TableHead className="text-right">Brecha (cargos − recargas)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {months.map((m) => (
                  <TableRow key={m.month}>
                    <TableCell>{m.month}</TableCell>
                    <TableCell className="text-right">{money(m.charges)}</TableCell>
                    <TableCell className="text-right">{money(m.topups)}</TableCell>
                    <TableCell className={`text-right ${m.gap > 0 ? 'text-amber-600' : tone(m.gap)}`}>{money(m.gap)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        <div className="mt-3">
          <Hint>
            Brecha positiva: ese mes se gastó más de lo que se recargó (el saldo bajó). Negativa: se recargó más de lo
            gastado (el saldo subió). El saldo estimado solo suma lo que ve la app; no incluye el saldo inicial previo.
          </Hint>
        </div>
      </ChartCard>

      <ChartCard
        title="Recargas recientes"
        subtitle="Monto de cada recarga (las fallidas en rojo)."
        filename="redo-recargas-recientes"
        csv={recent.map((r) => ({ Fecha: r.date, Estado: r.status, Monto: r.amount }))}
      >
        {recent.length === 0 ? (
          empty('Sin recargas.')
        ) : (
          <ComboChart
            data={recent}
            xKey="date"
            height={240}
            xFormat={(v) => String(v).slice(5)}
            series={[
              { key: 'ok', label: 'Exitosas', type: 'bar', color: '#2563eb', format: money },
              { key: 'failed', label: 'Fallidas', type: 'bar', color: '#dc2626', format: money },
            ]}
          />
        )}
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Facturas</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Periodo</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Cargos</TableHead>
                  <TableHead className="text-right">Saldo inicial</TableHead>
                  <TableHead className="text-right">Saldo final</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.bills.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin facturas.'}
                    </TableCell>
                  </TableRow>
                )}
                {(d?.bills ?? []).map((b) => {
                  const st = billStatus(b.status)
                  return (
                    <TableRow key={b.id}>
                      <TableCell className="whitespace-nowrap">
                        {day(b.period_start)} a {day(b.period_end)}
                      </TableCell>
                      <TableCell>
                        <Pill label={st.label} className={st.pill} />
                      </TableCell>
                      <TableCell className="text-right">{money(b.charge_total)}</TableCell>
                      <TableCell className="text-right">{money(b.opening_balance)}</TableCell>
                      <TableCell className={`text-right ${tone(b.closing_balance)}`}>{money(b.closing_balance)}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Últimas recargas</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Monto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(t?.recent.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin recargas.'}
                    </TableCell>
                  </TableRow>
                )}
                {(t?.recent ?? []).map((r, i) => {
                  const ok = (r.status ?? '').toUpperCase() === 'SUCCESS'
                  return (
                    <TableRow key={`${r.created_at}-${i}`}>
                      <TableCell>{day(r.created_at)}</TableCell>
                      <TableCell>
                        <Pill label={ok ? 'Exitosa' : (r.status ?? '—')} className={ok ? PILL.green : PILL.red} />
                      </TableCell>
                      <TableCell className="text-right">{money(r.amount)}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
