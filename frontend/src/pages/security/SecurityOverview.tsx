import { ShieldCheck } from 'lucide-react'

import { Callout, ErrorAlert, Kpi } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { security, urgentSteps, type SecurityOverview as OverviewData, type SecurityTabId, type UrgentStep } from '@/lib/security'
import { useAsync } from '@/lib/useAsync'

import { RISK_ORDER, RISK_STYLE } from './securityFormat'
import { LoadingNote, SecurityHeader } from './securityUi'

const OK_TEXT = 'text-emerald-600'
const BAD_TEXT = 'text-red-600'
const tone = (count: number) => (count > 0 ? BAD_TEXT : OK_TEXT)

function RiskLight({ risk }: { risk: OverviewData['risk'] }) {
  const style = RISK_STYLE[risk.level]
  return (
    <div role="status" className={`flex flex-wrap items-center gap-4 rounded-lg border p-4 ${style.box}`}>
      <div className="flex gap-1.5 rounded-full bg-slate-900 p-2" aria-hidden="true">
        {RISK_ORDER.map((level) => (
          <span key={level} className={`size-5 rounded-full ${level === risk.level ? RISK_STYLE[level].light : 'bg-slate-600/60'}`} />
        ))}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-lg font-semibold">{style.title}</div>
        <p className="text-sm">{risk.reason}</p>
      </div>
    </div>
  )
}

function UrgentList({ steps, onOpenTab }: { steps: UrgentStep[]; onOpenTab: (tab: SecurityTabId) => void }) {
  if (steps.length === 0) return <Callout tone="info">No hay nada urgente: ni secretos por rotar, ni permisos de escritura sobrantes, ni vencimientos cercanos.</Callout>
  return (
    <ol className="space-y-3">
      {steps.map((step, i) => (
        <li key={step.id} className="flex flex-wrap items-center gap-3 rounded-md border p-3">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-slate-800 text-sm font-semibold text-white" aria-hidden="true">
            {i + 1}
          </span>
          <div className="min-w-0 flex-1">
            <div className="font-medium">{step.title}</div>
            <p className="text-muted-foreground text-sm">{step.detail}</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => onOpenTab(step.tab)}>
            {step.tabLabel}
          </Button>
        </li>
      ))}
    </ol>
  )
}

function WhyItMatters() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="size-5" aria-hidden="true" /> Por qué importa
        </CardTitle>
      </CardHeader>
      <CardContent className="text-muted-foreground max-w-3xl space-y-2 text-sm">
        <p>
          <strong className="text-foreground">Esta app solo lee.</strong> Un token con permisos de escritura podría modificar tu tienda si se filtra.
        </p>
        <p>
          Un token es como una llave de la tienda: con permisos de lectura, quien lo encuentre solo puede mirar. Con permisos de escritura o eliminación también podría cambiar precios, vaciar el inventario, borrar productos o escribir a tus creadores usando tu nombre.
        </p>
        <p>
          Por eso conviene dejar solo los permisos que la app usa y cambiar (rotar) las claves que alguna vez se compartieron en un chat. Aquí nunca se muestran los valores de las claves: solo sus nombres, fechas y estados.
        </p>
      </CardContent>
    </Card>
  )
}

/** Semáforo global de seguridad, cifras clave y los pasos más urgentes con acceso directo a su pestaña. */
export function SecurityOverview({ onOpenTab }: { onOpenTab: (tab: SecurityTabId) => void }) {
  const { data, loading, error } = useAsync(() => security.overview(), [])

  return (
    <div className="space-y-4">
      <SecurityHeader title="Resumen de seguridad" note="Estado de las credenciales y permisos de todas las plataformas conectadas." />
      <ErrorAlert message={error} />
      {loading && !data && <LoadingNote />}
      {data && (
        <>
          <RiskLight risk={data.risk} />
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi
              title="Permisos de escritura que la app no usa"
              value={String(data.unused_write_scopes)}
              hint={`${data.write_scopes_granted} de escritura o eliminación concedidos en total`}
              className={tone(data.unused_write_scopes)}
            />
            <Kpi
              title="Plataformas pendientes de rotar"
              value={String(data.pending_rotation_count)}
              hint="Claves expuestas que aún no se cambiaron"
              className={tone(data.pending_rotation_count)}
            />
            <Kpi
              title="Credenciales que vencen pronto"
              value={String(data.upcoming_expirations.length)}
              hint="Tokens de renovación por vencer o vencidos"
              className={tone(data.upcoming_expirations.length)}
            />
            <Kpi
              title="Archivos con secretos sin ignorar por git"
              value={String(data.files_not_git_ignored.length)}
              hint="Podrían subirse a un repositorio"
              className={tone(data.files_not_git_ignored.length)}
            />
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Lo más urgente</CardTitle>
            </CardHeader>
            <CardContent>
              <UrgentList steps={urgentSteps(data)} onOpenTab={onOpenTab} />
            </CardContent>
          </Card>
        </>
      )}
      <WhyItMatters />
    </div>
  )
}
