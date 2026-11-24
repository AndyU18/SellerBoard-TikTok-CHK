import { useMemo, useState } from 'react'

import { ErrorAlert, Kpi } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { Donut } from '@/components/charts/Donut'
import { axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { type WithdrawalRow, tiktokMoney, withdrawalAmountByStatus } from '@/lib/tiktokMoney'
import { useAsync } from '@/lib/useAsync'

import type { SortState } from '../walmart/walmartSort'
import { compareValues, CsvButton, nextSort, PageTitle, SearchBox, SortHead } from '../walmart/walmartUi'
import { monthLabel, NUM_CELL, shortDate, statusLabel } from './tiktokMoneyFormat'
import { EmptyRows, StatusPill } from './tiktokMoneyUi'

const NO_WITHDRAWALS = 'No hay retiros de TikTok Shop en este período.'

type SortKey = 'created_at' | 'withdrawal_id' | 'type' | 'status' | 'amount'
const TEXT_KEYS: readonly SortKey[] = ['withdrawal_id', 'type', 'status']

const sortValue = (w: WithdrawalRow, key: SortKey): string | number | null => (key === 'status' ? statusLabel(w.status) : w[key])

const csvRow = (w: WithdrawalRow) => ({
  'ID de retiro': w.withdrawal_id,
  Fecha: w.created_at ?? '',
  Tipo: w.type ?? '',
  Estado: statusLabel(w.status),
  Monto: w.amount.toFixed(2),
  Moneda: w.currency ?? '',
})

function WithdrawalsTable({ rows, sort, onSort }: { rows: WithdrawalRow[]; sort: SortState<SortKey>; onSort: (k: SortKey) => void }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortHead sortKey="created_at" label="Fecha" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="withdrawal_id" label="ID de retiro" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="type" label="Tipo" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="amount" label="Monto" sort={sort} onSort={onSort} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((w) => (
          <TableRow key={w.withdrawal_id}>
            <TableCell className="whitespace-nowrap">{shortDate(w.created_at)}</TableCell>
            <TableCell className="font-mono text-xs">{w.withdrawal_id}</TableCell>
            <TableCell>{w.type ?? '—'}</TableCell>
            <TableCell>
              <StatusPill status={w.status} />
            </TableCell>
            <TableCell className={NUM_CELL}>
              {money(w.amount)} {w.currency && w.currency !== 'USD' && <span className="text-muted-foreground text-xs">{w.currency}</span>}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function TikTokWithdrawals() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const report = useAsync(() => tiktokMoney.withdrawals(days), [days])
  const data = report.data
  const s = data?.summary

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'created_at', dir: -1 })

  const all = useMemo(() => data?.withdrawals ?? [], [data])
  const months = useMemo(() => (data?.by_month ?? []).map((m) => ({ ...m, label: monthLabel(m.month) })), [data])
  const slices = useMemo(
    () => withdrawalAmountByStatus(all).map((x) => ({ id: x.status, label: statusLabel(x.status), value: x.amount })),
    [all],
  )
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return all
      .filter((w) => !q || [w.withdrawal_id, w.type ?? '', statusLabel(w.status)].some((v) => v.toLowerCase().includes(q)))
      .sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [all, search, sort])

  const statusCounts = Object.entries(s?.by_status ?? {})
  const empty = (msg: string) => <EmptyChart>{report.loading ? 'Cargando…' : msg}</EmptyChart>

  return (
    <div className="space-y-6">
      <PageTitle title="Retiros" scope={description} note="Dinero que sacaste de tu saldo de TikTok Shop hacia tu banco." />
      <ErrorAlert message={report.error} />

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Retirado" value={s ? money(s.total_amount) : '—'} hint={s ? `${count(s.withdrawals)} retiros` : undefined} />
        {statusCounts.map(([status, n]) => (
          <Kpi key={status} title={`Retiros ${statusLabel(status).toLowerCase()}`} value={count(n)} hint="cantidad de retiros en ese estado" />
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={`Retiros por mes — ${description}`}
          subtitle="Barras: monto retirado. Línea (eje derecho): cantidad de retiros."
          filename="tiktok-retiros-mensuales"
          csv={months.map((m) => ({ Mes: m.month, Retiros: m.count, Monto: m.amount }))}
        >
          {months.length === 0 ? (
            empty(NO_WITHDRAWALS)
          ) : (
            <ComboChart
              data={months}
              xKey="label"
              height={260}
              rightFormat={axisNumber}
              series={[
                { key: 'amount', label: 'Monto', type: 'bar', color: COLOR.blue, format: money },
                { key: 'count', label: 'Retiros', type: 'line', axis: 'right', color: COLOR.amber, format: count },
              ]}
            />
          )}
        </ChartCard>
        <ChartCard
          title={`Monto por estado — ${description}`}
          subtitle="Cómo se reparte lo retirado según el estado del retiro."
          filename="tiktok-retiros-por-estado"
          csv={slices.map((x) => ({ Estado: x.label, Monto: x.value }))}
        >
          {slices.length === 0 ? empty(NO_WITHDRAWALS) : <Donut slices={slices} format={money} centerLabel="retirado" />}
        </ChartCard>
      </div>

      <Card>
        <CardContent className="space-y-3 overflow-x-auto pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium">Detalle de retiros</span>
            <div className="flex items-center gap-2">
              <SearchBox value={search} onChange={setSearch} placeholder="Buscar por ID, tipo o estado" />
              <CsvButton filename="tiktok-retiros.csv" rows={rows.map(csvRow)} />
            </div>
          </div>
          {rows.length > 0 ? (
            <WithdrawalsTable rows={rows} sort={sort} onSort={(k) => setSort((cur) => nextSort(cur, k, TEXT_KEYS))} />
          ) : (
            <EmptyRows loading={report.loading} message={all.length ? 'Ningún retiro coincide con la búsqueda.' : NO_WITHDRAWALS} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
