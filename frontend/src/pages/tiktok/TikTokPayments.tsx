import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, Pill, PILL } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { type PaymentRow, type ReconcileState, paymentsByMonth, reconcileGaps, reconcileState, tiktokMoney } from '@/lib/tiktokMoney'
import { useAsync } from '@/lib/useAsync'

import { compareValues, CsvButton, nextSort, PageTitle, SearchBox, SortHead } from '../walmart/walmartUi'
import type { SortState } from '../walmart/walmartSort'
import { maskedAccount, monthLabel, NUM_CELL, shortDate, statusLabel } from './tiktokMoneyFormat'
import { EmptyRows, StatusPill } from './tiktokMoneyUi'

const NO_PAYMENTS = 'No hay pagos de TikTok Shop en este período.'
/** Cuántos pagos recientes se dibujan en las barras de cuadre (más no se leen). */
const RECONCILE_BARS_MAX = 18

type SortKey = 'created_at' | 'amount' | 'reserve_amount' | 'exchange_rate' | 'status' | 'difference'
const TEXT_KEYS: readonly SortKey[] = ['status']

const RECONCILE_PILL: Record<ReconcileState, { label: string; className: string }> = {
  ok: { label: 'Cuadra', className: PILL.green },
  difference: { label: 'Con diferencia', className: PILL.red },
  unverifiable: { label: 'Sin cuadre', className: PILL.slate },
}

const sortValue = (p: PaymentRow, key: SortKey): string | number | null => (key === 'status' ? statusLabel(p.status) : p[key])

const csvRow = (p: PaymentRow) => ({
  'ID de pago': p.payment_id,
  'Fecha': p.created_at ?? '',
  'Fecha de pago': p.paid_at ?? '',
  Monto: p.amount.toFixed(2),
  Moneda: p.currency ?? '',
  'Monto en USD': p.settlement_amount.toFixed(2),
  Retención: p.reserve_amount.toFixed(2),
  'Tipo de cambio': p.exchange_rate ?? '',
  Estado: statusLabel(p.status),
  Cuenta: maskedAccount(p.bank_last4),
  Liquidaciones: p.statements,
  'Liquidaciones faltantes': p.missing_statements,
  Liquidado: p.settled_amount?.toFixed(2) ?? '',
  Diferencia: p.difference?.toFixed(2) ?? '',
  Cuadre: RECONCILE_PILL[reconcileState(p)].label,
})

/** Barras por pago: liquidado vs pagado (verde si cuadra, rojo si no). Solo pagos con cuadre calculable. */
function reconcileChartData(rows: PaymentRow[]) {
  return rows
    .filter((p) => p.checkable)
    .slice(0, RECONCILE_BARS_MAX)
    .reverse()
    .map((p) => ({
      label: `${shortDate(p.created_at)} · ${p.payment_id.slice(-4)}`,
      settled: p.settled_amount,
      paidOk: p.reconciled ? p.settlement_amount : null,
      paidOff: p.reconciled ? null : p.settlement_amount,
      difference: p.difference,
    }))
}

function GapNotice({ rows }: { rows: PaymentRow[] }) {
  const gaps = reconcileGaps(rows)
  if (!gaps.unverifiable) return null
  return (
    <Callout tone="warning">
      <strong>{count(gaps.unverifiable)}</strong> de {count(rows.length)} pagos no se pueden cuadrar porque falta alguna
      liquidación enlazada
      {gaps.missingStatements > 0 && ` (${count(gaps.missingStatements)} liquidaciones por importar)`}
      {gaps.withoutStatements > 0 && `; ${count(gaps.withoutStatements)} pagos no traen liquidaciones asociadas`}. Con una liquidación
      faltante la diferencia saldría falsa, así que no se calcula hasta que estén todas importadas.
    </Callout>
  )
}

function PaymentsTable({ rows, sort, onSort }: { rows: PaymentRow[]; sort: SortState<SortKey>; onSort: (k: SortKey) => void }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortHead sortKey="created_at" label="Fecha" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="amount" label="Monto" sort={sort} onSort={onSort} />
          <SortHead sortKey="reserve_amount" label="Retención" sort={sort} onSort={onSort} />
          <SortHead sortKey="exchange_rate" label="Tipo de cambio" sort={sort} onSort={onSort} />
          <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
          <TableHead>Cuenta</TableHead>
          <SortHead sortKey="difference" label="Cuadre" sort={sort} onSort={onSort} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((p) => {
          const state = RECONCILE_PILL[reconcileState(p)]
          return (
            <TableRow key={p.payment_id}>
              <TableCell className="whitespace-nowrap">{shortDate(p.created_at)}</TableCell>
              <TableCell className={NUM_CELL}>
                {money(p.amount)} {p.currency && p.currency !== 'USD' && <span className="text-muted-foreground text-xs">{p.currency}</span>}
              </TableCell>
              <TableCell className={NUM_CELL}>{money(p.reserve_amount)}</TableCell>
              <TableCell className={NUM_CELL}>{p.exchange_rate == null ? '—' : p.exchange_rate.toFixed(4)}</TableCell>
              <TableCell>
                <StatusPill status={p.status} />
              </TableCell>
              <TableCell className="whitespace-nowrap tabular-nums">{maskedAccount(p.bank_last4)}</TableCell>
              <TableCell className="text-right">
                <div className="flex flex-col items-end gap-0.5">
                  <Pill label={state.label} className={state.className} />
                  {p.difference != null && (
                    <span className={`text-xs tabular-nums ${p.reconciled ? 'text-muted-foreground' : 'text-red-700'}`}>{money(p.difference)}</span>
                  )}
                </div>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

export function TikTokPayments() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const report = useAsync(() => tiktokMoney.payments(days), [days])
  const data = report.data
  const s = data?.summary
  const rec = s?.reconciliation

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'created_at', dir: -1 })

  const all = useMemo(() => data?.payments ?? [], [data])
  const months = useMemo(() => paymentsByMonth(all).map((m) => ({ ...m, label: monthLabel(m.month) })), [all])
  const reconcileBars = useMemo(() => reconcileChartData(all), [all])
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return all
      .filter((p) => !q || [p.payment_id, statusLabel(p.status), p.bank_last4 ?? ''].some((v) => v.toLowerCase().includes(q)))
      .sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [all, search, sort])

  const withDifference = rec ? rec.checkable - rec.reconciled : 0
  const empty = (msg: string) => <EmptyChart>{report.loading ? 'Cargando…' : msg}</EmptyChart>

  return (
    <div className="space-y-6">
      <PageTitle title="Pagos a tu banco" scope={description} note="Lo que TikTok Shop depositó (o retiene) y si cuadra con tus liquidaciones." />
      <ErrorAlert message={report.error} />
      {data && <GapNotice rows={all} />}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Pagado" value={s ? money(s.paid_amount) : '—'} hint={s ? `${count(s.by_status.PAID ?? 0)} pagos completados` : undefined} />
        <Kpi title="Retenido" value={s ? money(s.reserve_amount) : '—'} hint="reserva que TikTok guarda de cada pago" />
        <Kpi
          title="Pendiente"
          value={s ? money(s.pending_amount) : '—'}
          hint="pagos aún no depositados"
          className={s && s.pending_amount > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Pagos con diferencia"
          value={rec ? count(withDifference) : '—'}
          hint={rec ? `de ${count(rec.checkable)} que se pudieron cuadrar (±${money(rec.tolerance_usd)})` : undefined}
          className={withDifference > 0 ? 'text-red-600' : ''}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={`Pagos y retención por mes — ${description}`}
          subtitle="Barras: pagado y retenido, según el mes en que TikTok creó el pago."
          filename="tiktok-pagos-mensuales"
          csv={months.map((m) => ({ Mes: m.month, Pagado: m.paid, Retenido: m.reserve }))}
        >
          {months.length === 0 ? (
            empty(NO_PAYMENTS)
          ) : (
            <ComboChart
              data={months}
              xKey="label"
              height={260}
              series={[
                { key: 'paid', label: 'Pagado', type: 'bar', color: COLOR.good, format: money },
                { key: 'reserve', label: 'Retenido', type: 'bar', color: COLOR.amber, format: money },
              ]}
            />
          )}
        </ChartCard>
        <ChartCard
          title="Cuadre: liquidado vs pagado"
          subtitle={`Por pago (últimos ${RECONCILE_BARS_MAX} con cuadre). Verde: coincide con las liquidaciones; rojo: hay diferencia.`}
          filename="tiktok-cuadre-pagos"
          csv={reconcileBars.map((b) => ({ Pago: b.label, Liquidado: b.settled, Pagado: b.paidOk ?? b.paidOff, Diferencia: b.difference }))}
        >
          {reconcileBars.length === 0 ? (
            empty('Ningún pago tiene todas sus liquidaciones importadas todavía.')
          ) : (
            <ComboChart
              data={reconcileBars}
              xKey="label"
              height={260}
              series={[
                { key: 'settled', label: 'Liquidado', type: 'bar', color: COLOR.blue, format: money },
                { key: 'paidOk', label: 'Pagado (cuadra)', type: 'bar', color: COLOR.good, format: money },
                { key: 'paidOff', label: 'Pagado (con diferencia)', type: 'bar', color: COLOR.bad, format: money },
              ]}
            />
          )}
        </ChartCard>
      </div>

      {rec && rec.checkable > 0 && (
        <Hint>
          En los {count(rec.checkable)} pagos cuadrables TikTok pagó {money(rec.paid)} y las liquidaciones suman {money(rec.settled)}: diferencia
          total {money(rec.difference)}. Positivo = TikTok pagó de más.
        </Hint>
      )}

      <Card>
        <CardContent className="space-y-3 overflow-x-auto pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium">Detalle de pagos</span>
            <div className="flex items-center gap-2">
              <SearchBox value={search} onChange={setSearch} placeholder="Buscar por ID, estado o cuenta" />
              <CsvButton filename="tiktok-pagos.csv" rows={rows.map(csvRow)} />
            </div>
          </div>
          {rows.length > 0 ? (
            <PaymentsTable rows={rows} sort={sort} onSort={(k) => setSort((cur) => nextSort(cur, k, TEXT_KEYS))} />
          ) : (
            <EmptyRows loading={report.loading} message={all.length ? 'Ningún pago coincide con la búsqueda.' : NO_PAYMENTS} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
