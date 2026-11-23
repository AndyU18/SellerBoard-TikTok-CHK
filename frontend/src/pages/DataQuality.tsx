import { ErrorAlert, Hint, PILL, Pill } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count, money } from '@/lib/format'
import { skus, type QualityCheck, type QualitySeverity } from '@/lib/skus'
import { useAsync } from '@/lib/useAsync'

const SEVERITY: Record<QualitySeverity, { label: string; pill: string; border: string }> = {
  critical: { label: 'Crítico', pill: PILL.red, border: 'border-l-red-500' },
  warning: { label: 'Advertencia', pill: PILL.amber, border: 'border-l-amber-500' },
  info: { label: 'Informativo', pill: PILL.blue, border: 'border-l-blue-500' },
  ok: { label: 'En orden', pill: PILL.green, border: 'border-l-green-500' },
}

const scoreColor = (score: number) =>
  score >= 90 ? 'text-green-600' : score >= 70 ? 'text-amber-600' : 'text-red-600'

function CheckRow({ check, onNavigate }: { check: QualityCheck; onNavigate?: (page: string) => void }) {
  const s = SEVERITY[check.severity]
  return (
    <Card className={`border-l-4 ${s.border}`}>
      <CardContent className="flex flex-wrap items-start justify-between gap-3 pt-4">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{check.title}</span>
            <Pill label={s.label} className={s.pill} />
            {check.severity !== 'ok' && (
              <span className="text-muted-foreground text-xs">{count(check.count)} afectados</span>
            )}
            {check.impact != null && check.impact > 0 && (
              <span className="text-xs font-medium">{money(check.impact)} involucrados</span>
            )}
          </div>
          <p className="text-muted-foreground text-sm">{check.detail}</p>
        </div>
        {check.severity !== 'ok' && onNavigate && (
          <Button size="sm" variant="outline" onClick={() => onNavigate(check.action.page)}>
            {check.action.label}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

export function DataQuality({ onNavigate }: { onNavigate?: (page: string) => void }) {
  const data = useAsync(() => skus.quality(), [])
  const d = data.data
  const open = (d?.checks ?? []).filter((c) => c.severity !== 'ok')
  const healthy = (d?.checks ?? []).filter((c) => c.severity === 'ok')

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Calidad de datos</h1>
      <Hint>
        Revisiones automáticas de datos faltantes o mal unidos que distorsionan la utilidad. La puntuación resta 20
        puntos por cada revisión crítica, 8 por advertencia y 2 por informativa.
      </Hint>
      <ErrorAlert message={data.error} />
      {data.loading && !d && <p className="text-muted-foreground text-sm">Calculando…</p>}

      {d && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Puntuación de calidad</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-6">
              <div className={`text-6xl font-bold ${scoreColor(d.score)}`} aria-label={`Puntuación ${d.score} de 100`}>
                {d.score}
                <span className="text-muted-foreground text-2xl font-normal"> / 100</span>
              </div>
              <div className="space-y-2">
                <p className="font-medium">{d.summary.headline}</p>
                <div className="flex flex-wrap gap-2">
                  {(['critical', 'warning', 'info', 'ok'] as const).map((k) => (
                    <Pill key={k} label={`${d.summary[k]} ${SEVERITY[k].label.toLowerCase()}`} className={SEVERITY[k].pill} />
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          <section className="space-y-3">
            <h2 className="text-lg font-semibold">Requieren atención</h2>
            {open.length === 0 && <p className="text-muted-foreground text-sm">Nada pendiente: todas las revisiones están en orden.</p>}
            {open.map((c) => (
              <CheckRow key={c.id} check={c} onNavigate={onNavigate} />
            ))}
          </section>

          {healthy.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-lg font-semibold">En orden</h2>
              {healthy.map((c) => (
                <CheckRow key={c.id} check={c} />
              ))}
            </section>
          )}
        </>
      )}
    </div>
  )
}
