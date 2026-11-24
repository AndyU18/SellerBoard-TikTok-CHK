import { useMemo, useState } from 'react'

import { ErrorAlert } from '@/components/analytics/ui'
import { useScopeDescription } from '@/components/PageHeader'
import { ChartCard, EmptyChart, type CsvRow } from '@/components/charts/kit'
import { ExpandableTable } from '@/components/charts/ExpandableTable'
import { buildRows } from '@/components/dashboard/statementRows'
import { api, type Filters } from '@/lib/api'
import { isoDate } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type PeriodId = 'today' | 'yesterday' | 'month' | 'lastMonth' | '12m' | 'global'

const TABS: { id: PeriodId; label: string }[] = [
  { id: 'today', label: 'Hoy' },
  { id: 'yesterday', label: 'Ayer' },
  { id: 'month', label: 'Este mes' },
  { id: 'lastMonth', label: 'Mes pasado' },
  { id: '12m', label: 'Últimos 12 meses' },
  { id: 'global', label: 'Período global' },
]

function rangeOf(id: PeriodId, global: { start?: string; end?: string }): { start?: string; end?: string } {
  const now = new Date()
  const day = (offset: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset)
  switch (id) {
    case 'today':
      return { start: isoDate(day(0)), end: isoDate(day(0)) }
    case 'yesterday':
      return { start: isoDate(day(-1)), end: isoDate(day(-1)) }
    case 'month':
      return {
        start: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)),
        end: isoDate(day(0)),
      }
    case 'lastMonth':
      return {
        start: isoDate(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
        end: isoDate(new Date(now.getFullYear(), now.getMonth(), 0)),
      }
    case '12m':
      return {
        start: isoDate(new Date(now.getFullYear(), now.getMonth() - 11, 1)),
        end: isoDate(day(0)),
      }
    default:
      return global
  }
}

/** Estado de resultados con pestañas de período (Hoy, Ayer, Este mes...), respetando canal y tienda globales. */
export function IncomeStatement({ className = '' }: { className?: string }) {
  const scope = useScope()
  const description = useScopeDescription()
  const [tab, setTab] = useState<PeriodId>('global')
  const range = rangeOf(tab, scope.range)

  const data = useAsync(() => {
    const f: Filters = {
      ...range,
      channel: scope.channelParam,
      account: scope.account,
    }
    return api.profit('channel', f)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, range.start, range.end, scope.channelParam, scope.account])

  const totals = data.data?.totals ?? null
  const rows = useMemo(() => (totals ? buildRows(totals, data.data?.rows ?? []) : []), [totals, data.data])
  const label = TABS.find((t) => t.id === tab)?.label ?? ''
  const csv: CsvRow[] = rows.flatMap((r) => [
    { concepto: r.label, monto: r.cells[0], porcentaje_ventas: r.cells[1] },
    ...(r.children ?? []).map((c) => ({
      concepto: `  ${c.label}`,
      monto: c.cells[0],
      porcentaje_ventas: c.cells[1],
    })),
  ])

  return (
    <ChartCard
      title={`Estado de resultados — ${description}`}
      subtitle={`${label}${range.start ? ` · ${range.start} a ${range.end}` : ' · todo el historial'}. Toca Ventas para ver el desglose por canal.`}
      csv={csv}
      filename={`estado-de-resultados-${tab}`}
      className={className}
    >
      <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label="Período del estado de resultados">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-pressed={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded border px-2 py-1 text-xs ${tab === t.id ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <ErrorAlert message={data.error} />
      {data.loading && !data.data ? (
        <EmptyChart>Cargando…</EmptyChart>
      ) : !totals ? (
        !data.error && <EmptyChart>Sin ventas en este período.</EmptyChart>
      ) : (
        <ExpandableTable columns={['Monto', '% ventas']} rows={rows} />
      )}
    </ChartCard>
  )
}
