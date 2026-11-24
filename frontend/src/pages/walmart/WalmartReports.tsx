import { Download } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, Segmented } from '@/components/analytics/ui'
import { EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, percent } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'
import { downloadCsv, walmartCatalog, type CsvRecord, type ReportColumnFill, type ReportRequest, type ReportType } from '@/lib/walmartCatalog'

const REPORTS_LIMIT = 100
const READY_STATUS = 'READY'
const NO_VALUE = '—'
const POOR_FILL = 0.5 // columnas con menos de la mitad de filas llenas se marcan como faltantes
const MAX_CELL_WIDTH = 'max-w-56'

const ROW_OPTIONS = [25, 50, 100] as const
type RowLimit = (typeof ROW_OPTIONS)[number]
const DEFAULT_ROWS: RowLimit = 25

type TypeFilter = 'all' | ReportType
const REPORT_TYPES: ReportType[] = ['ITEM', 'BUYBOX']

const STATUS_PILL: Record<string, string> = { READY: PILL.green, ERROR: PILL.red, INPROGRESS: PILL.blue, RECEIVED: PILL.blue }

const isReady = (r: ReportRequest) => (r.status ?? '').toUpperCase() === READY_STATUS

function formatDate(iso: string | null): string {
  if (!iso) return NO_VALUE
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? NO_VALUE : d.toLocaleString('es', { dateStyle: 'medium', timeStyle: 'short' })
}

const requestCsv = (r: ReportRequest): CsvRecord => ({
  id_solicitud: r.request_id,
  tipo: r.report_type,
  estado: r.status,
  version: r.report_version,
  solicitado: r.submitted_at,
  generado: r.generated_at,
})

/** De menos a más completa; las columnas sin filas (null) al final. */
const byFillAscending = (a: ReportColumnFill, b: ReportColumnFill) => (a.filled_pct ?? 2) - (b.filled_pct ?? 2) || a.name.localeCompare(b.name)

function RequestsTable({ rows, selectedId, onSelect }: { rows: ReportRequest[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Tipo</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead>Solicitado</TableHead>
          <TableHead>Generado</TableHead>
          <TableHead>
            <span className="sr-only">Vista previa</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.request_id} className={selectedId === r.request_id ? 'bg-blue-50' : ''}>
            <TableCell>
              <div className="font-medium">{r.report_type ?? NO_VALUE}</div>
              <div className="text-muted-foreground font-mono text-xs">{r.request_id}</div>
            </TableCell>
            <TableCell>
              <Pill label={r.status ?? 'Sin estado'} className={STATUS_PILL[(r.status ?? '').toUpperCase()] ?? PILL.slate} />
            </TableCell>
            <TableCell>{formatDate(r.submitted_at)}</TableCell>
            <TableCell>{formatDate(r.generated_at)}</TableCell>
            <TableCell className="text-right">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!isReady(r)}
                aria-pressed={selectedId === r.request_id}
                title={isReady(r) ? undefined : 'Solo se pueden ver reportes ya generados (READY)'}
                onClick={() => onSelect(r.request_id)}
              >
                Vista previa
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Completitud por columna: barra del porcentaje de filas con valor, de la menos a la más completa. */
function FillBars({ columns }: { columns: ReportColumnFill[] }) {
  const sorted = useMemo(() => [...columns].sort(byFillAscending), [columns])
  return (
    <ul className="max-h-96 space-y-1.5 overflow-y-auto pr-2" aria-label="Completitud por columna">
      {sorted.map((c) => {
        const pct = c.filled_pct
        const poor = pct != null && pct < POOR_FILL
        return (
          <li key={c.name} className="grid grid-cols-[minmax(0,12rem)_1fr_3.5rem] items-center gap-2 text-xs sm:grid-cols-[minmax(0,16rem)_1fr_4rem]">
            <span className="truncate" title={c.name}>
              {c.name}
            </span>
            <span className="h-2 overflow-hidden rounded bg-slate-100" aria-hidden="true">
              <span className={`block h-full ${poor ? 'bg-red-500' : 'bg-emerald-500'}`} style={{ width: `${(pct ?? 0) * 100}%` }} />
            </span>
            <span className={`text-right tabular-nums ${poor ? 'font-medium text-red-700' : ''}`}>{pct == null ? NO_VALUE : percent(pct)}</span>
          </li>
        )
      })}
    </ul>
  )
}

function PreviewTable({ headers, rows }: { headers: string[]; rows: Record<string, string | number | boolean | null>[] }) {
  return (
    <div className="max-h-96 overflow-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            {headers.map((h) => (
              <TableHead key={h} className="whitespace-nowrap">
                {h}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {headers.map((h) => (
                <TableCell key={h} className={`${MAX_CELL_WIDTH} truncate text-xs`} title={String(row[h] ?? '')}>
                  {row[h] == null || row[h] === '' ? <span className="text-muted-foreground">{NO_VALUE}</span> : String(row[h])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** Vista previa bajo demanda de un reporte generado: filas de muestra y completitud por columna. */
function ReportPreviewPanel({ requestId }: { requestId: string }) {
  const [limit, setLimit] = useState<RowLimit>(DEFAULT_ROWS)
  const preview = useAsync(() => walmartCatalog.reportPreview(requestId, limit), [requestId, limit])
  const data = preview.data
  const poorColumns = data?.columns.filter((c) => c.filled_pct != null && c.filled_pct < POOR_FILL).length ?? 0

  return (
    <Card aria-busy={preview.loading}>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <div>
          <CardTitle className="text-base">Vista previa del reporte</CardTitle>
          <p className="text-muted-foreground mt-1 font-mono text-xs">{requestId}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            label="Filas de la vista previa"
            value={String(limit) as `${RowLimit}`}
            options={ROW_OPTIONS.map((n) => ({ value: String(n) as `${RowLimit}`, label: `${n} filas` }))}
            onChange={(v) => setLimit(Number(v) as RowLimit)}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!data?.rows.length}
            onClick={() => data && downloadCsv(`walmart-reporte-${requestId}.csv`, data.rows)}
          >
            <Download className="mr-1 size-4" /> CSV
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Hint>Solo lectura: se descarga el reporte ya generado para mirarlo, nunca se crea ni se modifica nada en Walmart.</Hint>
        <ErrorAlert message={preview.error} />
        {preview.loading && <EmptyChart>Descargando y leyendo el reporte…</EmptyChart>}
        {data && !preview.loading && (
          <>
            <section aria-label="Resumen del reporte" className="grid gap-3 sm:grid-cols-3">
              <Kpi title="Filas del reporte" value={count(data.total_rows)} hint={`Se muestran ${count(data.preview_rows)}`} />
              <Kpi title="Columnas" value={count(data.headers.length)} />
              <Kpi
                title="Columnas incompletas"
                value={count(poorColumns)}
                hint={`Con menos de ${percent(POOR_FILL)} de filas llenas`}
                className={poorColumns > 0 ? 'text-red-600' : ''}
              />
            </section>
            {data.rows.length === 0 ? <EmptyChart>El reporte no trae filas.</EmptyChart> : <PreviewTable headers={data.headers} rows={data.rows} />}
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Completitud por columna</h3>
              <Hint>Porcentaje de filas con valor, de menos a más completa: las primeras columnas son las que más datos le faltan al catálogo.</Hint>
              <FillBars columns={data.columns} />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

/** Reportes de Walmart (Reports API): solicitudes existentes y vista previa de los ya generados. Solo lectura. */
export function WalmartReports() {
  const description = useScopeDescription()
  const [filter, setFilter] = useState<TypeFilter>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const data = useAsync(() => walmartCatalog.reports(REPORTS_LIMIT), [])
  const report = data.data

  const requests = useMemo(() => report?.requests ?? [], [report])
  const countOf = (t: ReportType) => requests.filter((r) => r.report_type === t).length
  const visible = useMemo(() => (filter === 'all' ? requests : requests.filter((r) => r.report_type === filter)), [requests, filter])
  const missingTypes = report ? REPORT_TYPES.filter((t) => countOf(t) === 0) : []
  const ready = requests.filter(isReady).length
  const loading = data.loading && !report

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Reportes (Reports API)</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
        <Hint>
          Lista las solicitudes de reporte de Walmart y permite ver una muestra de las que ya están generadas. Esta pantalla solo lee y descarga: nunca crea reportes.
        </Hint>
      </header>

      <ErrorAlert message={data.error} />

      <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-3">
        <Kpi title="Solicitudes" value={report ? count(requests.length) : loading ? 'Cargando…' : NO_VALUE} />
        <Kpi title="Listas para ver" value={report ? count(ready) : loading ? 'Cargando…' : NO_VALUE} hint="Estado READY" />
        <Kpi
          title="Por tipo"
          value={report ? REPORT_TYPES.map((t) => `${t} ${countOf(t)}`).join(' · ') : loading ? 'Cargando…' : NO_VALUE}
        />
      </section>

      {loading && <EmptyChart>Cargando…</EmptyChart>}

      {report && (
        <>
          {missingTypes.map((t) => (
            <Callout key={t} tone="info">
              No hay solicitudes de reporte {t}; crear reportes requiere una acción de escritura que esta app no hace.
            </Callout>
          ))}

          <Card>
            <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
              <CardTitle className="text-base">Solicitudes</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <Segmented
                  label="Filtrar por tipo"
                  value={filter}
                  options={[
                    { value: 'all', label: 'Todos', count: requests.length },
                    ...REPORT_TYPES.map((t) => ({ value: t, label: t, count: countOf(t) })),
                  ]}
                  onChange={setFilter}
                />
                <Button type="button" variant="outline" size="sm" disabled={!visible.length} onClick={() => downloadCsv('walmart-reportes.csv', visible.map(requestCsv))}>
                  <Download className="mr-1 size-4" /> CSV
                </Button>
              </div>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              {visible.length === 0 ? (
                <EmptyChart>No hay solicitudes de este tipo.</EmptyChart>
              ) : (
                <RequestsTable rows={visible} selectedId={selectedId} onSelect={setSelectedId} />
              )}
            </CardContent>
          </Card>

          {selectedId && <ReportPreviewPanel key={selectedId} requestId={selectedId} />}
        </>
      )}
    </div>
  )
}
