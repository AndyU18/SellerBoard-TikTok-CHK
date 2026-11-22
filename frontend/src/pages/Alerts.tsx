import { useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type AlertSeverity } from '@/lib/api'
import { loadAlerts } from '@/lib/alerts'
import { useAsync } from '@/lib/useAsync'

const SEVERITY: Record<AlertSeverity, { label: string; box: string; badge: string }> = {
  critical: { label: 'Crítica', box: 'border-red-300 bg-red-50', badge: 'bg-red-100 text-red-800' },
  warning: { label: 'Atención', box: 'border-amber-300 bg-amber-50', badge: 'bg-amber-100 text-amber-800' },
  info: { label: 'Aviso', box: 'border-slate-200 bg-slate-50', badge: 'bg-slate-100 text-slate-700' },
}

const CATEGORY: Record<string, string> = {
  sync: 'Sincronización',
  profit: 'Utilidad',
  costs: 'Costos',
  inventory: 'Inventario',
  performance: 'Desempeño del vendedor',
  returns: 'Devoluciones',
  logistics: 'Logística',
  cashflow: 'Flujo de caja',
}

const RUN_STATUS: Record<string, { label: string; badge: string }> = {
  success: { label: 'Correcta', badge: 'bg-green-100 text-green-800' },
  partial: { label: 'Con avisos', badge: 'bg-amber-100 text-amber-800' },
  failed: { label: 'Falló', badge: 'bg-red-100 text-red-800' },
  running: { label: 'En curso', badge: 'bg-blue-100 text-blue-800' },
}

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('es-MX', { dateStyle: 'short', timeStyle: 'short' }) : '—'

function interval(minutes: number) {
  return minutes % 60 === 0 ? `${minutes / 60} h` : `${minutes} min`
}

export function Alerts() {
  const [version, setVersion] = useState(0)
  const [only, setOnly] = useState<AlertSeverity | 'all'>('all')
  const alerts = useAsync(() => loadAlerts(version > 0), [version])
  const sync = useAsync(() => api.syncStatus(), [version])

  const counts = alerts.data?.counts
  const rows = (alerts.data?.alerts ?? []).filter((a) => only === 'all' || a.severity === only)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Alertas</h1>
        <Button variant="outline" size="sm" onClick={() => setVersion((v) => v + 1)} disabled={alerts.loading}>
          {alerts.loading ? 'Actualizando…' : 'Actualizar'}
        </Button>
      </div>

      {alerts.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {alerts.error}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {(['all', 'critical', 'warning'] as const).map((s) => {
          const n = s === 'all' ? (counts ? counts.critical + counts.warning + counts.info : 0) : (counts?.[s] ?? 0)
          return (
            <button
              key={s}
              onClick={() => setOnly(s)}
              className={`rounded-md border px-3 py-1.5 text-sm ${only === s ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
            >
              {s === 'all' ? 'Todas' : SEVERITY[s].label + 's'} ({n})
            </button>
          )
        })}
      </div>

      <div className="space-y-2">
        {rows.length === 0 && (
          <p className="text-muted-foreground py-8 text-center text-sm">
            {alerts.loading ? 'Calculando…' : 'Todo en orden: no hay alertas con este filtro.'}
          </p>
        )}
        {rows.map((a) => (
          <div key={a.id} className={`rounded-md border p-3 text-sm ${SEVERITY[a.severity].box}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded px-2 py-0.5 text-xs ${SEVERITY[a.severity].badge}`}>{SEVERITY[a.severity].label}</span>
              <span className="text-muted-foreground text-xs">{CATEGORY[a.category] ?? a.category}</span>
              {a.channel && <ChannelBadge channel={a.channel as never} />}
              <strong>{a.title}</strong>
            </div>
            <p className="text-muted-foreground mt-1">{a.detail}</p>
          </div>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Sincronización automática</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 overflow-x-auto">
          {sync.data && (
            <p className="text-muted-foreground text-sm">
              {sync.data.auto_enabled
                ? `Activa: cada ${interval(sync.data.interval_minutes)} se vuelven a leer los últimos ${sync.data.days} días de cada plataforma (solo lectura).`
                : 'Apagada. Actívala con CHK_AUTO_SYNC=true en el .env del backend y reinícialo.'}
            </p>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Fuente</TableHead>
                <TableHead>Última ejecución</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Origen</TableHead>
                <TableHead>Próxima</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(sync.data?.sources ?? []).map((s) => {
                const st = s.status ? RUN_STATUS[s.status] : null
                return (
                  <TableRow key={s.source}>
                    <TableCell className="font-medium">{s.label}</TableCell>
                    <TableCell>{s.configured ? when(s.started_at) : 'Sin credenciales'}</TableCell>
                    <TableCell>
                      {st ? (
                        <span className={`rounded px-2 py-0.5 text-xs ${st.badge}`} title={s.error ?? undefined}>
                          {st.label}
                        </span>
                      ) : (
                        '—'
                      )}
                    </TableCell>
                    <TableCell>{s.triggered_by === 'schedule' ? 'Automática' : s.triggered_by ? 'Manual' : '—'}</TableCell>
                    <TableCell>{when(s.next_due_at)}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          <p className="text-muted-foreground text-xs">
            Las alertas se calculan en el momento con los datos sincronizados. Los umbrales (pérdida desde 3 unidades,
            devoluciones desde 20 %, menos de 14 días de inventario) y las metas de desempeño son de referencia.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
