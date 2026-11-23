import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money, tone } from '@/lib/format'
import { redo } from '@/lib/redo'
import { useAsync } from '@/lib/useAsync'

const day = (s: string | null | undefined) => (s ? s.slice(0, 10) : '—')

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
        <CardContent className="grid gap-4 sm:grid-cols-3">
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cargos y recargas por mes</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="h-72">
            {(d?.by_month.length ?? 0) === 0 ? (
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                {data.loading ? 'Cargando…' : 'Sin movimientos todavía.'}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={d?.by_month}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="month" />
                  <YAxis tickFormatter={(v: number) => `$${v.toLocaleString('en-US')}`} width={72} />
                  <Tooltip formatter={(v) => money(Number(v))} />
                  <Legend />
                  <Bar dataKey="charges" name="Cargos (costo)" fill="#dc2626" />
                  <Bar dataKey="topups" name="Recargas (financiamiento)" fill="#2563eb" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
          {(d?.by_month.length ?? 0) > 0 && (
            <div className="overflow-x-auto">
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
                  {(d?.by_month ?? []).map((m) => (
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
          <Hint>
            Brecha positiva: ese mes se gastó más de lo que se recargó (el saldo bajó). Negativa: se recargó más de lo
            gastado (el saldo subió).
          </Hint>
        </CardContent>
      </Card>

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
