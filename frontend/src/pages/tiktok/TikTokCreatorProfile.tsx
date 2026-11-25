import { CheckCircle2, CircleSlash, ShieldCheck } from 'lucide-react'

import { Callout, ErrorAlert, Hint, Pill, PILL } from '@/components/analytics/ui'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count, percent } from '@/lib/format'
import {
  type CreatorCoverage,
  type CreatorPermission,
  type CreatorProfileData,
  type CreatorStatus,
  type Expiry,
  type ExpiryTone,
  expiryOf,
  tiktokCreatorAccount,
} from '@/lib/tiktokCreatorAccount'
import { useAsync } from '@/lib/useAsync'

import { PageTitle } from '../walmart/walmartUi'
import { Fact, ProductImage } from './tiktokCatalogUi'
import { NO_VALUE, shortDateTime } from './tiktokFormat'

const REAUTH_HEADLINE = 'Hay que volver a autorizar la cuenta de creador'
const REAUTH_STEPS: readonly string[] = [
  'Entra al Partner Center de TikTok Shop con la cuenta de creador.',
  'Autoriza de nuevo la cuenta de creador con la misma app que ya usas.',
  'Espera a la próxima sincronización: la app detectará la nueva autorización sola.',
]
const REAUTH_NOTE =
  'Esta app no puede renovar ni crear la autorización por su cuenta: la autorización de Creator es única y su token de renovación cambia en cada uso.'

const CAN_DO: readonly string[] = [
  'Ver tus datos de cuenta de creador y los permisos que TikTok le concedió.',
  'Revisar los productos de tu vitrina y su estado de revisión.',
  'Seguir tus pedidos de afiliado y las comisiones cuando aparezcan.',
  'Saber qué parte del catálogo de afiliados está cargada en la app.',
]
const CANNOT_DO: readonly string[] = [
  'No pide muestras de productos.',
  'No crea colaboraciones ni invitaciones con vendedores.',
  'No genera enlaces de afiliado ni agrega productos a tu vitrina.',
  'No escribe nada en TikTok: todo es solo lectura.',
]

const COVERAGE_PARTIAL_WHY =
  'La API de TikTok entrega el catálogo de 20 en 20 y solo deja ver hasta el tope de productos por consulta; lo que queda fuera de ese tope no se puede cargar. Por eso la cobertura puede ser parcial.'

const EXPIRY_PILL: Record<ExpiryTone, { label: string; className: string }> = {
  ok: { label: 'Vigente', className: PILL.green },
  soon: { label: 'Vence pronto', className: PILL.amber },
  expired: { label: 'Vencido', className: PILL.red },
  unknown: { label: 'Sin dato', className: PILL.slate },
}

const daysText = ({ daysLeft, tone }: Expiry): string => {
  if (daysLeft === null) return NO_VALUE
  if (tone === 'expired') return `venció hace ${count(Math.abs(daysLeft))} días`
  return `faltan ${count(daysLeft)} días`
}

function ConnectionPills({ status }: { status: CreatorStatus }) {
  const state = status.needs_reauth
    ? { label: 'Hay que volver a autorizar', className: PILL.amber }
    : status.connected
      ? { label: 'Conectado', className: PILL.green }
      : { label: 'Sin conexión', className: PILL.slate }
  return (
    <div className="flex flex-wrap gap-2">
      <Pill label={status.configured ? 'Autorización guardada' : 'Falta la autorización'} className={status.configured ? PILL.green : PILL.red} />
      <Pill label={state.label} className={state.className} />
    </div>
  )
}

function ReauthNotice({ message }: { message: string }) {
  return (
    <Callout tone="danger">
      <p className="font-semibold">{REAUTH_HEADLINE}</p>
      <p className="mt-1">{message}</p>
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        {REAUTH_STEPS.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <p className="mt-2 text-xs">{REAUTH_NOTE}</p>
    </Callout>
  )
}

function ExpiryFact({ label, iso }: { label: string; iso: string | null | undefined }) {
  const expiry = expiryOf(iso)
  const pill = EXPIRY_PILL[expiry.tone]
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2 font-medium">
        {shortDateTime(iso)}
        <Pill label={pill.label} className={pill.className} />
        <span className="text-muted-foreground text-xs font-normal">{daysText(expiry)}</span>
      </dd>
    </div>
  )
}

function ConnectionCard({ status }: { status: CreatorStatus }) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">Estado de la conexión</CardTitle>
        <ConnectionPills status={status} />
      </CardHeader>
      <CardContent className="space-y-4">
        {status.needs_reauth ? <ReauthNotice message={status.message} /> : <Callout tone="info">{status.message}</Callout>}
        <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          <ExpiryFact label="El acceso vence" iso={status.tokens?.access_expires_at} />
          <ExpiryFact label="La autorización vence" iso={status.tokens?.refresh_expires_at} />
          <Fact label="Última sincronización ligera" value={shortDateTime(status.last_light_sync_at)} />
          <Fact label="Última sincronización completa" value={shortDateTime(status.last_full_sync_at)} />
          <Fact label="Próxima sincronización completa" value={shortDateTime(status.next_full_due_at)} />
        </dl>
        {status.last_run?.error && <Callout tone="warning">La última sincronización terminó con un aviso: {status.last_run.error}</Callout>}
      </CardContent>
    </Card>
  )
}

function CoverageCard({ coverage }: { coverage: CreatorCoverage }) {
  const fraction = coverage.pct === null ? null : Math.min(1, Math.max(0, coverage.pct))
  const partial = fraction === null || fraction < 1
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Catálogo de afiliados cargado</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-2xl font-semibold tabular-nums">{fraction === null ? NO_VALUE : percent(fraction)}</span>
          <span className="text-muted-foreground text-sm">
            {count(coverage.loaded)} cargados de {count(coverage.total)} · tope visible {count(coverage.visible_cap)}
          </span>
        </div>
        <div
          role="progressbar"
          aria-label="Cobertura del catálogo cargado"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}
          className="h-2.5 w-full overflow-hidden rounded-full bg-slate-200"
        >
          <div className="h-full rounded-full bg-emerald-500" style={{ width: `${(fraction ?? 0) * 100}%` }} />
        </div>
        {partial && <Callout tone="info">{COVERAGE_PARTIAL_WHY}</Callout>}
        <Hint>{coverage.note}</Hint>
      </CardContent>
    </Card>
  )
}

function ProfileCard({ profile }: { profile: CreatorProfileData }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Perfil</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <ProductImage src={profile.avatar_url} title={profile.username} className="size-20" />
        <dl className="grid flex-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          <Fact label="Usuario" value={profile.username ? `@${profile.username}` : NO_VALUE} />
          <Fact label="Tipo de cuenta" value={[profile.user_type, profile.seller_type].filter(Boolean).join(' · ') || NO_VALUE} />
          <Fact label="Región de registro" value={profile.register_region ?? NO_VALUE} />
          <Fact label="Región de selección" value={profile.selection_region ?? NO_VALUE} />
          <Fact label="Productos en el catálogo" value={count(profile.catalog_total)} />
          <Fact label="Productos en la vitrina" value={count(profile.showcase_total)} />
        </dl>
      </CardContent>
    </Card>
  )
}

function PermissionCard({ permission }: { permission: CreatorPermission }) {
  const Icon = permission.granted ? CheckCircle2 : CircleSlash
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <ShieldCheck className="text-muted-foreground size-4" aria-hidden />
          {permission.label}
        </CardTitle>
        <Pill label={permission.granted ? 'Concedido' : 'No concedido'} className={permission.granted ? PILL.green : PILL.slate} />
      </CardHeader>
      <CardContent className="flex gap-2 text-sm">
        <Icon className={`mt-0.5 size-4 shrink-0 ${permission.granted ? 'text-emerald-600' : 'text-slate-400'}`} aria-hidden />
        <div>
          <p>{permission.explanation}</p>
          <code className="text-muted-foreground text-xs">{permission.code}</code>
        </div>
      </CardContent>
    </Card>
  )
}

function GuideList({ title, items, tone }: { title: string; items: readonly string[]; tone: 'can' | 'cannot' }) {
  const Icon = tone === 'can' ? CheckCircle2 : CircleSlash
  const color = tone === 'can' ? 'text-emerald-600' : 'text-slate-400'
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm">
          {items.map((item) => (
            <li key={item} className="flex gap-2">
              <Icon className={`mt-0.5 size-4 shrink-0 ${color}`} aria-hidden />
              {item}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

/**
 * Cuenta de creador propia (afiliados): estado de la conexión y vencimientos, cobertura del catálogo cargado,
 * perfil, permisos explicados y una guía de qué hace y qué no hace la app. Solo lectura.
 */
export function TikTokCreatorProfile() {
  const description = useScopeDescription()
  const status = useAsync(() => tiktokCreatorAccount.status(), [])
  const profile = useAsync(() => tiktokCreatorAccount.profile(), [])
  const s = status.data
  const p = profile.data

  return (
    <div className="space-y-6">
      <PageTitle title="Cuenta de creador" scope={description} note="Tu cuenta de creador de TikTok Shop (afiliados): conexión, perfil y permisos. Solo lectura." />
      <ErrorAlert message={status.error} />
      <ErrorAlert message={profile.error} />
      {status.loading && !s && <p className="text-muted-foreground text-sm">Cargando…</p>}

      {s && (
        <div className="grid gap-4 xl:grid-cols-2">
          <ConnectionCard status={s} />
          <CoverageCard coverage={s.coverage} />
        </div>
      )}

      {p?.available && p.profile && <ProfileCard profile={p.profile} />}
      {p && !p.available && <Callout tone="info">Todavía no hay una foto del perfil de creador: aparecerá después de la primera sincronización.</Callout>}

      {p && p.permissions.length > 0 && (
        <section aria-labelledby="creator-permissions" className="space-y-3">
          <h2 id="creator-permissions" className="text-base font-semibold">
            Permisos de la cuenta
          </h2>
          <div className="grid gap-3 md:grid-cols-2">
            {p.permissions.map((permission) => (
              <PermissionCard key={permission.code} permission={permission} />
            ))}
          </div>
          <Hint>{p.permissions_note}</Hint>
        </section>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <GuideList title="Qué puedes hacer con esta cuenta" items={CAN_DO} tone="can" />
        <GuideList title="Qué NO hace esta app" items={CANNOT_DO} tone="cannot" />
      </div>
    </div>
  )
}
