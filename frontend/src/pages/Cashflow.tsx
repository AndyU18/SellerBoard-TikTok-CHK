import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ChannelBadge } from '@/components/ChannelBadge'
import { CashProjection } from '@/components/analytics/CashProjection'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { api, type Filters } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { count, money, PRESETS, presetRange, tone, type Preset } from '@/lib/format'
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
  const [preset, setPreset] = useState<Preset>('90d')
  const scope = useScope()
  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: scope.channelParam, account: scope.account }),
    [preset, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.cashflow(filters), [filters])

  const summary = data.data?.summary
  const rows = data.data?.rows ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Flujo de caja y liquidaciones</h1>
        <Select items={PRESETS} value={preset} onValueChange={(v) => v && setPreset(v as Preset)}>
          <SelectTrigger className="w-44" aria-label="Periodo">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRESETS.map((p) => (
              <SelectItem key={p.value} value={p.value}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="Las liquidaciones" />

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

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

      <CashProjection />

      <Card>
        <CardHeader>
          <CardTitle>Cobrado y por cobrar por mes</CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          {(data.data?.by_month.length ?? 0) === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              {data.loading ? 'Cargando…' : 'Sin liquidaciones. Sincroniza TikTok Shop desde Importar.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.data?.by_month}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="month" />
                <YAxis tickFormatter={(v: number) => `$${v.toLocaleString('en-US')}`} width={72} />
                <Tooltip formatter={(v) => money(Number(v))} />
                <Legend />
                <Bar dataKey="received" name="Cobrado" stackId="cash" fill="#16a34a" />
                <Bar dataKey="pending" name="Por cobrar" stackId="cash" fill="#f59e0b" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

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
