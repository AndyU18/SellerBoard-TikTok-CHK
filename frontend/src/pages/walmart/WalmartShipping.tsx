import { Download } from 'lucide-react'
import { useMemo } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart, axisMoney } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { downloadCsv, walmartCatalog, type CsvRecord, type ShippingMethod, type ShippingTemplate } from '@/lib/walmartCatalog'

const ACTIVE_STATUS = 'ACTIVE'
const NO_VALUE = '—'

const SERIES: ComboSeries[] = [
  { key: 'charged', label: 'Lo que cobras al comprador', type: 'bar', color: COLOR.blue },
  { key: 'label', label: 'Lo que cuesta la etiqueta (Redo)', type: 'bar', color: COLOR.amber },
]

const methodName = (m: ShippingMethod) => m.ship_method ?? 'Sin nombre'
const templateName = (t: ShippingTemplate) => t.name ?? t.id
const regionsText = (m: ShippingMethod) => m.regions.filter((r): r is string => !!r).join(', ') || NO_VALUE
const isActive = (t: ShippingTemplate) => (t.status ?? '').toUpperCase() === ACTIVE_STATUS

/** Frase corta de lo que se absorbe (o se gana) por pedido de 1 artículo. */
function absorbedText(v: number | null): string {
  if (v == null) return 'Sin etiquetas para comparar'
  if (v > 0) return `Absorbes ${money(v)} por pedido`
  if (v < 0) return `Cobras ${money(-v)} más de lo que cuesta`
  return 'Cubre justo el costo'
}

const absorbedClass = (v: number | null) => (v == null ? 'text-muted-foreground' : v > 0 ? 'font-medium text-red-700' : 'text-green-700')

function chargeBreakdown(m: ShippingMethod): string {
  const parts = [
    m.flat_charge != null && `fija ${money(m.flat_charge)}`,
    m.charge_per_item != null && `por artículo ${money(m.charge_per_item)}`,
    m.charge_per_weight != null && `por peso ${money(m.charge_per_weight)}${m.weight_unit ? ` / ${m.weight_unit}` : ''}`,
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Sin tarifa'
}

function csvRows(templates: ShippingTemplate[], avgLabel: number | null): CsvRecord[] {
  return templates.flatMap((t) =>
    t.methods.map((m) => ({
      plantilla: templateName(t),
      estado_plantilla: t.status,
      metodo: methodName(m),
      regiones: regionsText(m),
      dias_transito: m.transit_days,
      tarifa_fija: m.flat_charge?.toFixed(2),
      tarifa_por_articulo: m.charge_per_item?.toFixed(2),
      tarifa_por_peso: m.charge_per_weight?.toFixed(2),
      cobrado_pedido_1_articulo: m.charged_per_single_item_order.toFixed(2),
      costo_etiqueta_promedio: avgLabel?.toFixed(2),
      absorbido_por_pedido: m.absorbed_vs_label?.toFixed(2),
    })),
  )
}

function MethodsTable({ methods }: { methods: ShippingMethod[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Método</TableHead>
          <TableHead>Región</TableHead>
          <TableHead className="text-right">Tránsito</TableHead>
          <TableHead className="text-right">Tarifa al comprador</TableHead>
          <TableHead>Detalle de la tarifa</TableHead>
          <TableHead>Contra la etiqueta</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {methods.map((m, i) => (
          <TableRow key={`${methodName(m)}-${i}`}>
            <TableCell className="font-medium">{methodName(m)}</TableCell>
            <TableCell>{regionsText(m)}</TableCell>
            <TableCell className="text-right">{m.transit_days == null ? NO_VALUE : `${m.transit_days} d`}</TableCell>
            <TableCell className="text-right font-medium">
              {m.charged_per_single_item_order === 0 ? 'Gratis' : money(m.charged_per_single_item_order)}
            </TableCell>
            <TableCell className="text-muted-foreground text-xs">{chargeBreakdown(m)}</TableCell>
            <TableCell className={`text-xs ${absorbedClass(m.absorbed_vs_label)}`}>{absorbedText(m.absorbed_vs_label)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function TemplateCard({ template }: { template: ShippingTemplate }) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="text-base">{templateName(template)}</CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">{[template.type, template.rate_model_type].filter(Boolean).join(' · ') || 'Sin tipo'}</p>
        </div>
        <Pill label={template.status ?? 'Sin estado'} className={isActive(template) ? PILL.green : PILL.slate} />
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {template.methods.length > 0 ? (
          <MethodsTable methods={template.methods} />
        ) : (
          <p className="text-muted-foreground text-sm">{template.has_detail ? 'La plantilla no tiene métodos de envío.' : 'Aún no se descargó el detalle de esta plantilla.'}</p>
        )}
      </CardContent>
    </Card>
  )
}

/** Plantillas de envío de Walmart y lo que cobran al comprador contra el costo real de la etiqueta en Redo. */
export function WalmartShipping() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const data = useAsync(() => walmartCatalog.shippingTemplates(days), [days])
  const report = data.data

  const templates = useMemo(() => report?.templates ?? [], [report])
  const avgLabel = report?.redo_label.avg_label_cost ?? null
  const methods = useMemo(() => templates.flatMap((t) => t.methods.map((m) => ({ template: t, method: m }))), [templates])
  const totalMethods = methods.length
  const freeMethods = report?.summary.free_shipping_methods ?? 0

  const chartRows = useMemo(
    () =>
      avgLabel == null
        ? []
        : methods.map(({ template, method }) => ({
            name: `${templateName(template)} · ${methodName(method)}`,
            charged: method.charged_per_single_item_order,
            label: avgLabel,
          })),
    [methods, avgLabel],
  )

  const absorbing = methods.filter(({ method }) => (method.absorbed_vs_label ?? 0) > 0)
  const worst = absorbing.reduce<number>((max, { method }) => Math.max(max, method.absorbed_vs_label ?? 0), 0)
  const loading = data.loading && !report
  const placeholder = loading ? 'Cargando…' : NO_VALUE

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Plantillas de envío de Walmart</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
        <Hint>{report?.comparison_note ?? 'Compara lo que cada plantilla cobra al comprador contra el costo real promedio de las etiquetas de Redo en pedidos de Walmart.'}</Hint>
      </header>

      <ErrorAlert message={data.error} />

      <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Plantillas" value={report ? count(report.summary.templates) : placeholder} hint={report ? `${count(report.summary.active)} activas` : undefined} />
        <Kpi title="Métodos de envío" value={report ? count(totalMethods) : placeholder} />
        <Kpi
          title="Envío gratis al comprador"
          value={report ? (freeMethods > 0 ? 'Sí' : 'No') : placeholder}
          hint={report ? (freeMethods > 0 ? `${count(freeMethods)} de ${count(totalMethods)} métodos no cobran envío` : 'Todos los métodos cobran envío') : undefined}
          className={freeMethods > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Costo de etiqueta (Redo)"
          value={report ? money(avgLabel) : placeholder}
          hint={report ? `Promedio de ${count(report.redo_label.orders)} pedidos de Walmart en ${days} días` : undefined}
        />
      </section>

      {report?.redo_label.note && <Callout tone="warning">{report.redo_label.note}</Callout>}

      {avgLabel != null && totalMethods > 0 && (
        <Callout tone={absorbing.length > 0 ? 'danger' : 'info'}>
          {absorbing.length > 0
            ? `${count(absorbing.length)} de ${count(totalMethods)} métodos cobran menos de lo que cuesta la etiqueta (${money(avgLabel)} en promedio): el vendedor absorbe hasta ${money(worst)} por pedido de 1 artículo.`
            : `Todos los métodos cobran al menos lo que cuesta la etiqueta (${money(avgLabel)} en promedio): no se absorbe envío en pedidos de 1 artículo.`}
        </Callout>
      )}

      {loading && <EmptyChart>Cargando…</EmptyChart>}
      {report && templates.length === 0 && <EmptyChart>No hay plantillas de envío sincronizadas.</EmptyChart>}

      {report && templates.length > 0 && (
        <>
          {chartRows.length > 0 && (
            <ChartCard
              title="Lo que cobras vs lo que cuesta"
              subtitle="Por método, en un pedido de 1 artículo. Si la barra azul es menor que la ámbar, absorbes la diferencia."
              csv={chartRows.map((r) => ({ metodo: r.name, cobrado: r.charged.toFixed(2), costo_etiqueta: r.label.toFixed(2) }))}
              filename="walmart-envio-cobrado-vs-etiqueta"
            >
              <ComboChart data={chartRows} xKey="name" series={SERIES} leftFormat={axisMoney} />
            </ChartCard>
          )}

          <div className="flex justify-end">
            <Button type="button" variant="outline" size="sm" onClick={() => downloadCsv('walmart-plantillas-envio.csv', csvRows(templates, avgLabel))} disabled={totalMethods === 0}>
              <Download className="mr-1 size-4" /> CSV
            </Button>
          </div>

          <div className="space-y-4">
            {templates.map((t) => (
              <TemplateCard key={t.id} template={t} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
