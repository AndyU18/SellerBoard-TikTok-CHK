import { useState } from 'react'

import { DownloadButton } from '@/components/analytics/DownloadButton'
import { Callout, ErrorAlert, Hint } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { CHANNEL_LABEL, CHANNELS, type Channel } from '@/lib/api'
import { downloadExport, exportsApi, type ExportItem, type ExportParam, type ExportParams } from '@/lib/exports'
import { isoDate } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const SELECT = 'h-9 rounded-md border bg-white px-2 text-sm dark:bg-slate-900'
const DEFAULT_DAYS: Record<string, number> = { pricing_floor: 60, reorder: 30, returns_cost: 90, inventory: 30 }

type Range = { start: string; end: string }

/** Atajos de periodo para cerrar el mes o el año con el contador. */
const RANGES: { label: string; make: () => Range }[] = [
  {
    label: 'Mes anterior',
    make: () => {
      const n = new Date()
      return { start: isoDate(new Date(n.getFullYear(), n.getMonth() - 1, 1)), end: isoDate(new Date(n.getFullYear(), n.getMonth(), 0)) }
    },
  },
  {
    label: 'Este mes',
    make: () => {
      const n = new Date()
      return { start: isoDate(new Date(n.getFullYear(), n.getMonth(), 1)), end: isoDate(n) }
    },
  },
  {
    label: 'Este año',
    make: () => {
      const n = new Date()
      return { start: isoDate(new Date(n.getFullYear(), 0, 1)), end: isoDate(n) }
    },
  },
  {
    label: 'Año anterior',
    make: () => {
      const y = new Date().getFullYear() - 1
      return { start: `${y}-01-01`, end: `${y}-12-31` }
    },
  },
]

const PARAM_LABEL: Record<ExportParam, string> = {
  start: 'periodo',
  end: 'periodo',
  channel: 'canal',
  account: 'tienda',
  days: 'ventana en días',
}

function ExportCard({
  item,
  range,
  blocked,
  channel,
  account,
}: {
  item: ExportItem
  range: Range
  blocked: boolean
  channel: Channel | ''
  account: string
}) {
  const [days, setDays] = useState(DEFAULT_DAYS[item.name] ?? 30)
  const uses = (p: ExportParam) => item.params.includes(p)
  const daysValid = Number.isInteger(days) && days >= 7 && days <= 365

  const params: ExportParams = {
    start: uses('start') ? range.start || undefined : undefined,
    end: uses('end') ? range.end || undefined : undefined,
    channel: uses('channel') ? channel : '',
    account: uses('account') ? account || undefined : undefined,
    days: uses('days') ? days : undefined,
  }
  const applied = [...new Set(item.params.map((p) => PARAM_LABEL[p]))]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{item.title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">{item.description}</p>
        <p className="text-xs">
          <span className="text-muted-foreground">Usa: </span>
          {applied.length ? applied.join(', ') : 'sin filtros (datos al día de hoy)'}
        </p>
        {uses('days') && (
          <label className="flex w-40 flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Ventana (7 a 365 días)</span>
            <Input
              type="number"
              min={7}
              max={365}
              value={days}
              aria-invalid={!daysValid}
              onChange={(e) => setDays(Number(e.target.value))}
            />
          </label>
        )}
        <div className="flex flex-wrap gap-3">
          {item.formats.map((format) => (
            <DownloadButton
              key={format}
              label={format === 'csv' ? 'Descargar CSV' : 'Descargar PDF'}
              variant={format === 'pdf' ? 'default' : 'outline'}
              disabled={blocked || (uses('days') && !daysValid)}
              onDownload={() => downloadExport(item.name, format, params)}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export function ExportCenter() {
  const catalog = useAsync(() => exportsApi.catalog(), [])
  const scope = useScope()
  const [range, setRange] = useState<Range>(() => RANGES[0].make())
  const [channel, setChannel] = useState<Channel | ''>(scope.channelParam)
  const rangeInvalid = Boolean(range.start && range.end && range.start > range.end)

  const items = catalog.data ?? []
  const pdfs = items.filter((i) => !i.formats.includes('csv'))
  const csvs = items.filter((i) => i.formats.includes('csv'))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Exportar para el contador</h1>
        <Hint>
          Archivos listos para Excel (CSV con acentos correctos) y PDF para imprimir o enviar. El periodo y el canal
          aplican a las exportaciones que los usan.
        </Hint>
      </div>
      <ErrorAlert message={catalog.error} />

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Desde</span>
            <Input type="date" value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Hasta</span>
            <Input type="date" value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Canal</span>
            <select className={SELECT} value={channel} onChange={(e) => setChannel(e.target.value as Channel | '')}>
              <option value="">Todos</option>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  {CHANNEL_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Atajos de periodo">
            {RANGES.map((r) => (
              <button
                key={r.label}
                type="button"
                onClick={() => setRange(r.make())}
                className="rounded-md border px-3 py-1.5 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                {r.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setRange({ start: '', end: '' })}
              className="rounded-md border px-3 py-1.5 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              Todo el historial
            </button>
          </div>
        </CardContent>
      </Card>

      {rangeInvalid && <Callout tone="danger">La fecha inicial no puede ser posterior a la final.</Callout>}
      {scope.account && (
        <Callout tone="info">Las exportaciones por tienda usan la tienda elegida arriba: {scope.account}.</Callout>
      )}

      {catalog.loading && <p className="text-muted-foreground text-sm">Cargando exportaciones…</p>}

      {pdfs.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-medium">Reportes en PDF</h2>
          <div className="grid gap-4 lg:grid-cols-2">
            {pdfs.map((item) => (
              <ExportCard key={item.name} item={item} range={range} blocked={rangeInvalid} channel={channel} account={scope.account} />
            ))}
          </div>
        </section>
      )}

      {csvs.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-medium">Tablas en CSV</h2>
          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {csvs.map((item) => (
              <ExportCard key={item.name} item={item} range={range} blocked={rangeInvalid} channel={channel} account={scope.account} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
