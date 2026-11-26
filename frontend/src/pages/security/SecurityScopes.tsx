import { RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Pill, PILL, Segmented } from '@/components/analytics/ui'
import type { CsvRow } from '@/components/charts/kit'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/lib/auth'
import {
  captureSellerScopes,
  platformLabel,
  type CaptureResult,
  type PlatformScopes,
  type ScopeLevel,
  type ScopeRecommendation,
  type ScopeRow,
  type ScopesResponse,
  security,
} from '@/lib/security'
import { useAsync } from '@/lib/useAsync'
import { CsvButton } from '@/pages/walmart/walmartUi'

import { dateTime, EDIT_PERMISSION, LEVEL_BADGE, LEVEL_EXPLANATION, LEVEL_ORDER, NO_EDIT_PERMISSION_HINT, yesNo } from './securityFormat'
import { BadgePill, LoadingNote, SecurityHeader } from './securityUi'

const CAPTURE_CONFIRM =
  'Se renovará una vez el token del vendedor de TikTok Shop y se guardarán los NOMBRES de sus permisos (nunca el token). ¿Continuar?'
const LEVEL_TEXT_TONE: Readonly<Record<ScopeLevel, string>> = { read: 'text-emerald-600', write: 'text-amber-600', delete: 'text-red-600' }

/** Plataforma que se abre primero: la que tiene más permisos de escritura sobrantes; si no hay, la primera. */
function defaultPlatform(platforms: PlatformScopes[]): string | null {
  const worst = [...platforms].sort((a, b) => b.unused_write_count - a.unused_write_count)[0]
  return worst?.platform ?? null
}

const hasRemovable = (rec: ScopeRecommendation) => rec.remove_now.length > 0 || rec.remove_optional.length > 0

function LevelCards({ scopes }: { scopes: ScopeRow[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {LEVEL_ORDER.map((level) => {
        const count = scopes.filter((s) => s.level === level).length
        return (
          <Card key={level}>
            <CardHeader>
              <CardTitle className="text-sm font-medium">{LEVEL_BADGE[level].label}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-semibold ${count > 0 || level === 'read' ? LEVEL_TEXT_TONE[level] : 'text-emerald-600'}`}>{count}</div>
              <div className="text-muted-foreground text-xs">{LEVEL_EXPLANATION[level]}</div>
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}

function ScopeTable({ rows }: { rows: ScopeRow[] }) {
  if (rows.length === 0) return <p className="text-muted-foreground text-sm">Ningún permiso coincide con los filtros.</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Permiso</TableHead>
          <TableHead>Qué permite</TableHead>
          <TableHead>Nivel</TableHead>
          <TableHead>La app lo usa</TableHead>
          <TableHead>Qué daño podría causar si se filtra</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((s) => (
          <TableRow key={s.scope}>
            <TableCell className="align-top">
              <code className="text-xs break-all">{s.scope}</code>
              <div className="mt-1 flex flex-wrap gap-1">
                {!s.documented && <Pill label="Sin documentar" className={PILL.orange} />}
                {s.documented && !s.confirmed && <Pill label="A verificar" className={PILL.slate} />}
              </div>
            </TableCell>
            <TableCell className="align-top whitespace-normal">{s.allows}</TableCell>
            <TableCell className="align-top">
              <BadgePill badge={LEVEL_BADGE[s.level]} />
            </TableCell>
            <TableCell className="align-top">
              <Pill label={yesNo(s.used_by_app)} className={s.used_by_app ? PILL.green : PILL.slate} />
            </TableCell>
            <TableCell className="text-muted-foreground align-top whitespace-normal">{s.damage}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function ScopeList({ title, names }: { title: string; names: string[] }) {
  if (names.length === 0) return null
  return (
    <div>
      <div className="text-sm font-medium">{title}</div>
      <ul className="mt-1 flex flex-wrap gap-1.5">
        {names.map((n) => (
          <li key={n}>
            <code className="bg-muted rounded px-1.5 py-0.5 text-xs">{n}</code>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Permisos que sobran y guía para quitarlos. La app no puede quitarlos sola: se desmarcan en el portal. */
function RemovableScopes({ rec, cannotRevoke }: { rec: ScopeRecommendation; cannotRevoke: string }) {
  if (!hasRemovable(rec)) return null
  return (
    <Card className="border-amber-300">
      <CardHeader>
        <CardTitle className="text-base">Permisos que puedes quitar</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm">{rec.summary}</p>
        <ScopeList title="Quitar ahora (escritura o eliminación)" names={rec.remove_now} />
        <ScopeList title="Opcional (lectura que la app no usa)" names={rec.remove_optional} />
        {rec.steps.length > 0 && (
          <div>
            <div className="text-sm font-medium">Paso a paso en Partner Center</div>
            <ol className="mt-1 list-decimal space-y-1 pl-5 text-sm">
              {rec.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
        )}
        <Callout tone="warning">
          <strong>La app NO puede quitar estos permisos sola.</strong> Se desmarcan a mano en el portal y después se reautoriza la tienda. {cannotRevoke}
        </Callout>
      </CardContent>
    </Card>
  )
}

function CaptureControl({ canEdit, onDone }: { canEdit: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<CaptureResult | null>(null)

  const capture = async () => {
    if (!window.confirm(CAPTURE_CONFIRM)) return
    setBusy(true)
    setError(null)
    try {
      setResult(await captureSellerScopes())
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" onClick={capture} disabled={!canEdit || busy} title={canEdit ? undefined : NO_EDIT_PERMISSION_HINT}>
          <RefreshCw className="size-4" aria-hidden="true" /> {busy ? 'Actualizando…' : 'Actualizar permisos del vendedor'}
        </Button>
        {!canEdit && <span className="text-muted-foreground text-xs">{NO_EDIT_PERMISSION_HINT}</span>}
      </div>
      {error && <Callout tone="danger">No se pudo actualizar: {error}</Callout>}
      {result && (
        <Callout tone="info">
          Permisos actualizados: {result.granted} concedidos, {result.write} de escritura o eliminación ({result.delete} de eliminación), {result.unused_write} de escritura que la app no usa y {result.undocumented} sin documentar.
        </Callout>
      )}
    </div>
  )
}

function UnverifiedNotice({ platforms }: { platforms: PlatformScopes[] }) {
  const declared = platforms.filter((p) => p.source === 'declared')
  if (declared.length === 0) return null
  return (
    <Callout tone="info">
      <strong>No se pueden verificar por API:</strong> {declared.map((p) => p.label).join(', ')}. Sus permisos están declarados, no verificados: confirma en el portal de cada plataforma que las claves sean de solo lectura.
    </Callout>
  )
}

function csvRows(platform: PlatformScopes): CsvRow[] {
  return platform.scopes.map((s) => ({
    Plataforma: platform.label,
    Permiso: s.scope,
    Nivel: s.level_label,
    'Qué permite': s.allows,
    'La app lo usa': yesNo(s.used_by_app),
    'Daño posible': s.damage,
  }))
}

function PlatformDetail({ data, platform, canEdit, onCaptured }: { data: ScopesResponse; platform: PlatformScopes; canEdit: boolean; onCaptured: () => void }) {
  const [onlyWrite, setOnlyWrite] = useState(false)
  const [onlyUnused, setOnlyUnused] = useState(false)
  const rows = useMemo(
    () => platform.scopes.filter((s) => (!onlyWrite || s.level !== 'read') && (!onlyUnused || !s.used_by_app)),
    [platform.scopes, onlyWrite, onlyUnused],
  )

  return (
    <div className="space-y-4">
      <div className="text-muted-foreground text-sm">
        {platform.source === 'captured' && `Permisos leídos de la plataforma el ${dateTime(platform.captured_at)}.`}
        {platform.source === 'declared' && 'Permisos declarados: la plataforma no informa los suyos, por eso no están verificados.'}
        {platform.source === 'not_captured' && 'Aún no se capturaron los permisos concedidos.'}
      </div>
      {platform.source === 'not_captured' && <Callout tone="warning">{platform.recommendation.summary}</Callout>}
      {platform.capturable && <CaptureControl canEdit={canEdit} onDone={onCaptured} />}
      <LevelCards scopes={platform.scopes} />
      <RemovableScopes rec={platform.recommendation} cannotRevoke={data.cannot_revoke_from_app} />
      {platform.source !== 'not_captured' && !hasRemovable(platform.recommendation) && <Callout tone="info">{platform.recommendation.summary}</Callout>}
      <ScopeList title="Permisos a revisar (sin documentar o por verificar)" names={platform.recommendation.review} />
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">Permisos de {platform.label}</CardTitle>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={onlyWrite} onChange={(e) => setOnlyWrite(e.target.checked)} /> Solo escritura
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={onlyUnused} onChange={(e) => setOnlyUnused(e.target.checked)} /> Solo sobrantes
            </label>
            <CsvButton filename={`permisos-${platform.platform}.csv`} rows={csvRows(platform)} />
          </div>
        </CardHeader>
        <CardContent>
          <ScopeTable rows={rows} />
        </CardContent>
      </Card>
    </div>
  )
}

/** Permisos de cada plataforma: nivel, qué permiten, cuáles sobran y cómo quitarlos en el portal. */
export function SecurityScopes() {
  const [reload, setReload] = useState(0)
  const [chosen, setChosen] = useState<string | null>(null)
  const { can } = useAuth()
  const canEdit = can(EDIT_PERMISSION)
  const { data, loading, error } = useAsync(() => security.scopes(), [reload])

  const current = data?.platforms.find((p) => p.platform === (chosen ?? defaultPlatform(data.platforms))) ?? null

  return (
    <div className="space-y-4">
      <SecurityHeader
        title="Permisos de las plataformas"
        note="Qué puede hacer cada token. Esta app solo lee: todo permiso de escritura o eliminación es riesgo sin beneficio."
      />
      <ErrorAlert message={error} />
      {loading && !data && <LoadingNote />}
      {data && (
        <>
          <UnverifiedNotice platforms={data.platforms} />
          <Segmented
            label="Plataforma"
            value={current?.platform ?? ''}
            onChange={setChosen}
            options={data.platforms.map((p) => ({ value: p.platform, label: platformLabel(p.platform), count: p.granted }))}
          />
          {current && <PlatformDetail key={current.platform} data={data} platform={current} canEdit={canEdit} onCaptured={() => setReload((n) => n + 1)} />}
        </>
      )}
    </div>
  )
}
