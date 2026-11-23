import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Callout, ErrorAlert, Hint, pct1 } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CHANNEL_LABEL, CHANNELS, type Channel } from '@/lib/api'
import { analytics, type PnlRow, type Recurrence } from '@/lib/analytics'
import { money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type Line = { key: keyof PnlRow; label: string; bold?: boolean; cost?: boolean }

const LINES: Line[] = [
  { key: 'gross_sales', label: 'Ventas brutas' },
  { key: 'promo', label: 'Promociones del vendedor', cost: true },
  { key: 'net_sales', label: 'Ventas netas', bold: true },
  { key: 'platform_fees', label: 'Comisiones de la plataforma', cost: true },
  { key: 'shipping_cost', label: 'Envío', cost: true },
  { key: 'refund_amount', label: 'Reembolsos', cost: true },
  { key: 'cogs', label: 'Costo del producto', cost: true },
  { key: 'gross_profit', label: 'Utilidad bruta', bold: true },
  { key: 'ad_spend', label: 'Publicidad', cost: true },
  { key: 'contribution', label: 'Contribución', bold: true },
  { key: 'expenses', label: 'Gastos indirectos', cost: true },
  { key: 'net_profit', label: 'Utilidad neta', bold: true },
  { key: 'margin', label: 'Margen sobre ventas netas' },
]

const RECURRENCE: Record<Recurrence, string> = {
  one_time: 'Una vez',
  monthly: 'Mensual',
  annual: 'Anual (1/12 por mes)',
}

const SELECT = 'h-9 rounded-md border bg-white px-2 text-sm dark:bg-slate-900'

function monthsAgo(n: number): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 7)
}

function Expenses({ onChange }: { onChange: () => void }) {
  const [version, setVersion] = useState(0)
  const expenses = useAsync(() => analytics.expenses(), [version])
  const categories = useAsync(() => analytics.expenseCategories(), [])
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({
    category_id: 0,
    description: '',
    amount: '',
    recurrence: 'monthly' as Recurrence,
    start_date: new Date().toISOString().slice(0, 10),
    end_date: '',
    channel: '' as Channel | '',
  })

  const categoryId = form.category_id || categories.data?.[0]?.id || 0

  async function add() {
    setError(null)
    const amount = Number(form.amount)
    if (!form.description.trim() || !form.amount || Number.isNaN(amount) || amount < 0) {
      setError('Escribe una descripción y un monto válido')
      return
    }
    try {
      await analytics.addExpense({
        category_id: categoryId,
        description: form.description.trim(),
        amount,
        recurrence: form.recurrence,
        start_date: form.start_date,
        end_date: form.end_date || null,
        channel: form.channel || null,
      })
      setForm((f) => ({ ...f, description: '', amount: '', end_date: '' }))
      setVersion((v) => v + 1)
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo guardar el gasto')
    }
  }

  async function remove(id: number) {
    setError(null)
    try {
      await analytics.deleteExpense(id)
      setVersion((v) => v + 1)
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo quitar el gasto')
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Gastos indirectos</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <ErrorAlert message={expenses.error} />
        {error && (
          <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Categoría</span>
            <select
              className={SELECT}
              value={categoryId}
              onChange={(e) => setForm((f) => ({ ...f, category_id: Number(e.target.value) }))}
            >
              {(categories.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Descripción</span>
            <Input
              className="h-9 w-56"
              value={form.description}
              placeholder="Redo, nómina, contador…"
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Monto (USD)</span>
            <Input
              type="number"
              min={0}
              className="h-9 w-28"
              value={form.amount}
              onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Frecuencia</span>
            <select
              className={SELECT}
              value={form.recurrence}
              onChange={(e) => setForm((f) => ({ ...f, recurrence: e.target.value as Recurrence }))}
            >
              {(Object.keys(RECURRENCE) as Recurrence[]).map((r) => (
                <option key={r} value={r}>
                  {RECURRENCE[r]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Desde</span>
            <Input
              type="date"
              className="h-9 w-40"
              value={form.start_date}
              onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Hasta (opcional)</span>
            <Input
              type="date"
              className="h-9 w-40"
              value={form.end_date}
              onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Canal</span>
            <select
              className={SELECT}
              value={form.channel}
              onChange={(e) => setForm((f) => ({ ...f, channel: e.target.value as Channel | '' }))}
            >
              <option value="">Repartir entre canales</option>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  Solo {CHANNEL_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          <Button size="sm" onClick={add}>
            Agregar gasto
          </Button>
        </div>

        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Descripción</TableHead>
                <TableHead>Categoría</TableHead>
                <TableHead>Frecuencia</TableHead>
                <TableHead>Vigencia</TableHead>
                <TableHead>Canal</TableHead>
                <TableHead className="text-right">Monto</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(expenses.data?.length ?? 0) === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground py-8 text-center">
                    {expenses.loading ? 'Cargando…' : 'Aún no registras gastos indirectos.'}
                  </TableCell>
                </TableRow>
              )}
              {(expenses.data ?? []).map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="font-medium">{e.description}</TableCell>
                  <TableCell>{e.category ?? '—'}</TableCell>
                  <TableCell>{RECURRENCE[e.recurrence]}</TableCell>
                  <TableCell>
                    {e.start_date} → {e.end_date ?? 'sin fin'}
                  </TableCell>
                  <TableCell>{e.channel ? <ChannelBadge channel={e.channel} /> : 'Todos'}</TableCell>
                  <TableCell className="text-right">{money(e.amount)}</TableCell>
                  <TableCell>
                    <Button variant="ghost" size="sm" onClick={() => remove(e.id)}>
                      Quitar
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}

export function Pnl() {
  const scope = useScope()
  const [start, setStart] = useState(monthsAgo(5))
  const [end, setEnd] = useState(monthsAgo(0))
  const [version, setVersion] = useState(0)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const data = useAsync(
    () => analytics.pnl(`${start}-01`, `${end}-01`, scope.channelParam),
    [start, end, scope.channelParam, version],
  )
  const d = data.data
  const months = useMemo(() => d?.months ?? [], [d])

  const fmt = (line: Line, row: PnlRow) => {
    const v = row[line.key]
    if (line.key === 'margin') return pct1(v)
    if (v == null) return '—'
    return money(line.cost && v !== 0 ? -v : v)
  }

  async function download() {
    setDownloadError(null)
    try {
      const blob = await analytics.pnlCsv(`${start}-01`, `${end}-01`, scope.channelParam)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'estado_de_resultados.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'No se pudo descargar')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Estado de resultados</h1>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Desde</span>
            <Input
              type="month"
              className="h-9 w-40"
              value={start}
              onChange={(e) => e.target.value && setStart(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Hasta</span>
            <Input
              type="month"
              className="h-9 w-40"
              value={end}
              onChange={(e) => e.target.value && setEnd(e.target.value)}
            />
          </label>
          <Button variant="outline" size="sm" onClick={download}>
            Descargar CSV
          </Button>
        </div>
      </div>

      <ErrorAlert message={data.error} />
      {downloadError && <Callout tone="danger">{downloadError}</Callout>}
      <Callout tone="info">
        La utilidad de las demás pantallas es la <strong>contribución</strong> (antes de gastos indirectos). Aquí se
        restan los gastos fijos que registres abajo para llegar a la utilidad neta del negocio.
      </Callout>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                {months.map((m) => (
                  <TableHead key={m.month} className="text-right">
                    {m.month.slice(0, 7)}
                  </TableHead>
                ))}
                <TableHead className="text-right">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {months.length === 0 && (
                <TableRow>
                  <TableCell colSpan={3} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin datos en este rango.'}
                  </TableCell>
                </TableRow>
              )}
              {months.length > 0 &&
                LINES.map((line) => (
                  <TableRow key={line.key} className={line.bold ? 'bg-slate-50 font-semibold dark:bg-slate-800/40' : ''}>
                    <TableCell>{line.label}</TableCell>
                    {months.map((m) => (
                      <TableCell
                        key={m.month}
                        className={`text-right ${line.bold ? tone(m.total[line.key]) : ''}`}
                      >
                        {fmt(line, m.total)}
                      </TableCell>
                    ))}
                    <TableCell className={`text-right ${line.bold && d ? tone(d.summary[line.key]) : ''}`}>
                      {d ? fmt(line, d.summary) : '—'}
                    </TableCell>
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {months.some((m) => m.expenses_by_category.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>Gastos indirectos por categoría</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Mes</TableHead>
                  <TableHead>Categorías</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {months
                  .filter((m) => m.expenses_by_category.length > 0)
                  .map((m) => (
                    <TableRow key={m.month}>
                      <TableCell>{m.month.slice(0, 7)}</TableCell>
                      <TableCell className="text-sm">
                        {m.expenses_by_category.map((c) => `${c.category}: ${money(c.amount)}`).join(' · ')}
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Expenses onChange={() => setVersion((v) => v + 1)} />

      <Hint>
        Un gasto puntual cuenta en el mes de su fecha; uno mensual, en cada mes de su vigencia; uno anual, en 1/12
        durante 12 meses. Si tiene canal, es todo de ese canal; si no, se reparte entre los canales según sus ventas
        netas del mes. {d?.note}
      </Hint>
    </div>
  )
}
