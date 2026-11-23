import { useMemo, useRef, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Callout, ErrorAlert, Hint, Kpi, NumberField, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics, type LandedImportResult } from '@/lib/analytics'
import { count, money, tone } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const WINDOWS = [
  { value: '30', label: '30 días' },
  { value: '60', label: '60 días' },
  { value: '90', label: '90 días' },
]

function ImportCard({ onDone }: { onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [apply, setApply] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<LandedImportResult | null>(null)

  async function submit() {
    const file = input.current?.files?.[0]
    if (!file) {
      setError('Elige primero un archivo .xlsx')
      return
    }
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(await analytics.importLanded(file, apply))
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo importar el archivo')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cargar landed cost (Excel)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground max-w-3xl text-sm">
          Acepta el reporte por contenedor (hoja <strong>BY CONTAINER</strong>: precio de fábrica, aranceles y flete por
          unidad) o una lista plana con las columnas <strong>ITEM</strong> y <strong>LANDED UNIT COST</strong>. Volver a
          cargar un archivo no duplica nada.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <input ref={input} type="file" accept=".xlsx,.xls" aria-label="Archivo de landed cost" className="text-sm" />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={apply} onChange={(e) => setApply(e.target.checked)} />
            Actualizar también el costo (COGS) de los productos
          </label>
          <Button size="sm" onClick={submit} disabled={busy}>
            {busy ? 'Importando…' : 'Importar'}
          </Button>
        </div>
        {error && (
          <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}
        {result && (
          <div role="status" className="rounded-md border border-green-300 bg-green-50 p-3 text-sm text-green-900">
            <strong>{result.kind === 'container_report' ? 'Reporte por contenedor' : 'Lista plana'}:</strong>{' '}
            {count(result.rows_read)} filas leídas
            {result.receipts_inserted != null &&
              `, ${count(result.receipts_inserted)} recepciones nuevas y ${count(result.receipts_updated ?? 0)} actualizadas`}
            {result.products_costed != null &&
              `. ${count(result.products_costed)} productos con costo (${count(result.cost_changes ?? 0)} cambios)`}
            .
            {(result.skus_without_product ?? 0) > 0 && (
              <span className="text-muted-foreground">
                {' '}
                {count(result.skus_without_product ?? 0)} SKU del archivo no son productos tuyos todavía (no se
                crearon).
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function Tariffs() {
  const [extra, setExtra] = useState(0.1)
  const [days, setDays] = useState(60)
  const [version, setVersion] = useState(0)
  const data = useAsync(() => analytics.tariffs(extra, days), [extra, days, version])
  const sm = data.data?.summary

  const chart = useMemo(
    () =>
      (data.data?.monthly ?? []).map((m) => ({
        month: m.month,
        Aranceles: m.duty_rate == null ? null : +(m.duty_rate * 100).toFixed(2),
        Flete: m.freight_rate == null ? null : +(m.freight_rate * 100).toFixed(2),
      })),
    [data.data],
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Aranceles y costos de importación</h1>
        <div className="flex flex-wrap items-end gap-4">
          <Segmented
            label="Ventana de ventas"
            value={String(days)}
            options={WINDOWS}
            onChange={(v) => setDays(Number(v))}
          />
          <NumberField
            label="Simular arancel extra"
            value={Math.round(extra * 100)}
            min={0}
            max={200}
            suffix="puntos"
            onCommit={(v) => setExtra(v / 100)}
          />
        </div>
      </div>

      <ErrorAlert message={data.error} />
      {data.data && !data.data.has_receipts && (
        <Callout tone="warning">
          <strong>Aún no hay recepciones de contenedores.</strong> Carga abajo el reporte de landed cost para ver cuánto
          pagas de aranceles y flete y simular su efecto.
        </Callout>
      )}
      {(sm?.turn_negative ?? 0) > 0 && (
        <Callout tone="danger">
          <strong>
            {count(sm?.turn_negative ?? 0)} productos pasarían a perder dinero con +{Math.round(extra * 100)} puntos de
            arancel.
          </strong>
        </Callout>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Productos analizados" value={count(sm?.skus ?? 0)} hint="con costo de contenedor y ventas" />
        <Kpi
          title="Utilidad hoy"
          value={money(sm?.profit_now ?? 0)}
          hint={`ventas de los últimos ${days} días`}
          className={tone(sm?.profit_now)}
        />
        <Kpi
          title={`Con +${Math.round(extra * 100)} puntos`}
          value={money(sm?.profit_new ?? 0)}
          hint="mismas unidades, mismo precio"
          className={tone(sm?.profit_new)}
        />
        <Kpi
          title="Cambio"
          value={money(sm?.profit_change ?? 0)}
          hint="lo que costaría el arancel extra"
          className={(sm?.profit_change ?? 0) < 0 ? 'text-red-600' : ''}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Efecto por producto</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Unid.</TableHead>
                <TableHead className="text-right">Fábrica</TableHead>
                <TableHead className="text-right">Arancel</TableHead>
                <TableHead className="text-right">Costo puesto</TableHead>
                <TableHead className="text-right">+ por unidad</TableHead>
                <TableHead className="text-right">Margen hoy → nuevo</TableHead>
                <TableHead className="text-right">Subir precio</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data.data?.rows.length ?? 0) === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Ningún producto vendido tiene costo de contenedor todavía.'}
                  </TableCell>
                </TableRow>
              )}
              {(data.data?.rows ?? []).map((r) => (
                <TableRow key={`${r.channel}-${r.sku}`}>
                  <TableCell>
                    <div className="flex min-w-48 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground flex items-center gap-2 text-xs">
                        {r.sku} <ChannelBadge channel={r.channel} />
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">{count(r.units)}</TableCell>
                  <TableCell className="text-right">{money(r.fob)}</TableCell>
                  <TableCell className="text-right">{pct1(r.duty_rate)}</TableCell>
                  <TableCell className="text-right">{money(r.landed_unit)}</TableCell>
                  <TableCell className="text-right">{money(r.delta_unit)}</TableCell>
                  <TableCell className="text-right">
                    <span className={tone(r.margin_now)}>{pct1(r.margin_now)}</span> →{' '}
                    <span className={tone(r.margin_new)}>{pct1(r.margin_new)}</span>{' '}
                    {r.turns_negative && <Pill label="pierde" className={PILL.red} />}
                  </TableCell>
                  <TableCell className="text-right">
                    {r.price_increase == null ? '—' : `+${money(r.price_increase)}`}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Aranceles y flete por mes (% del valor de fábrica)</CardTitle>
          </CardHeader>
          <CardContent className="h-72">
            {chart.length === 0 ? (
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                {data.loading ? 'Cargando…' : 'Sin recepciones.'}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chart}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="month" />
                  <YAxis tickFormatter={(v: number) => `${v}%`} width={48} />
                  <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} />
                  <Legend />
                  <Line type="monotone" dataKey="Aranceles" stroke="#dc2626" dot />
                  <Line type="monotone" dataKey="Flete" stroke="#2563eb" dot />
                </LineChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Contenedores por mes</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Mes</TableHead>
                  <TableHead className="text-right">Contenedores</TableHead>
                  <TableHead className="text-right">Mercancía</TableHead>
                  <TableHead className="text-right">Aranceles</TableHead>
                  <TableHead className="text-right">Flete</TableHead>
                  <TableHead className="text-right">Recargo total</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...(data.data?.monthly ?? [])].reverse().map((m) => (
                  <TableRow key={m.month}>
                    <TableCell>{m.month}</TableCell>
                    <TableCell className="text-right">{count(m.containers)}</TableCell>
                    <TableCell className="text-right">{money(m.goods)}</TableCell>
                    <TableCell className="text-right">{money(m.duties)}</TableCell>
                    <TableCell className="text-right">{money(m.freight)}</TableCell>
                    <TableCell className="text-right">{pct1(m.landed_rate)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <ImportCard onDone={() => setVersion((v) => v + 1)} />

      <Hint>
        El arancel extra se calcula sobre el valor de fábrica (FOB) por unidad, igual que el porcentaje de aranceles del
        reporte, y se resta de la utilidad de las unidades vendidas en la ventana. “Subir precio” es lo que habría que
        sumar al precio cobrado para conservar la utilidad por unidad, descontando comisión y reembolsos. El costo de
        producto que usa el resto de la aplicación sale de estas mismas recepciones (cada contenedor rige desde su fecha
        de llegada).
      </Hint>
    </div>
  )
}
