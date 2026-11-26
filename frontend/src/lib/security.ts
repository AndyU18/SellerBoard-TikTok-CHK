// Seguridad de credenciales y permisos (backend: app/credential_audit_api.py, prefijo /api/security).
// Importado por pages/SecurityHub y pages/security/*. Ninguna respuesta del backend trae valores de secretos:
// solo nombres, fechas, estados y conteos; este módulo tampoco expone ninguna forma de enviarlos.
import { authFetch, parse } from '@/lib/api'

export type SecurityTabId = 'overview' | 'scopes' | 'credentials' | 'rotation'

export type RiskLevel = 'low' | 'medium' | 'high'
export type ScopeLevel = 'read' | 'write' | 'delete'
/** captured = leído de la plataforma; declared = lo que se sabe sin poder verificarlo; not_captured = aún sin capturar. */
export type ScopeSource = 'captured' | 'declared' | 'not_captured'
export type CredentialKind = 'secret_key' | 'access_token' | 'refresh_token' | 'connection_string' | 'webhook_url'
export type CredentialLocation = 'env' | 'file'
export type RecommendedAction = 'capture' | 'remove' | 'trim' | 'verify' | 'none'

/** Nombres de las plataformas (espejo de PLATFORM_LABELS del backend); un id nuevo se muestra tal cual. */
const PLATFORM_LABELS: Readonly<Record<string, string>> = {
  tiktok_shop: 'TikTok Shop (vendedor)',
  tiktok_creator: 'TikTok Creator',
  tiktok_organic: 'TikTok orgánico',
  tiktok_ads: 'TikTok Ads',
  walmart: 'Walmart',
  redo: 'Redo',
  supabase: 'Supabase (base de datos)',
  anthropic: 'IA (Anthropic)',
  smtp: 'Correo (SMTP)',
  slack: 'Slack',
  digest_webhook: 'Webhook del resumen',
}

export const platformLabel = (id: string): string => PLATFORM_LABELS[id] ?? id

/** Plataforma cuyos permisos se pueden leer por API (captura segura). */
export const SELLER_PLATFORM = 'tiktok_shop'

// --- resumen -------------------------------------------------------------------------------------------------
export interface UpcomingExpiration {
  platform: string
  name: string
  expires_at: string | null
  days_left: number
}

export interface SecurityOverview {
  write_scopes_granted: number
  unused_write_scopes: number
  platforms_with_unused_write: string[]
  pending_rotation: string[]
  pending_rotation_count: number
  upcoming_expirations: UpcomingExpiration[]
  files_not_git_ignored: string[]
  risk: { level: RiskLevel; reason: string }
}

// --- permisos ------------------------------------------------------------------------------------------------
export interface ScopeRow {
  scope: string
  level: ScopeLevel
  level_label: string
  allows: string
  damage: string
  used_by_app: boolean
  documented: boolean
  confirmed: boolean
  unused: boolean
}

export interface ScopeRecommendation {
  action: RecommendedAction
  summary: string
  remove_now: string[]
  remove_optional: string[]
  review: string[]
  steps: string[]
  can_revoke_from_app: boolean
}

export interface PlatformScopes {
  platform: string
  label: string
  source: ScopeSource
  captured_at: string | null
  capturable: boolean
  granted: number
  write_count: number
  delete_count: number
  unused_count: number
  unused_write_count: number
  undocumented_count: number
  scopes: ScopeRow[]
  recommendation: ScopeRecommendation
}

export interface ScopesResponse {
  platforms: PlatformScopes[]
  cannot_revoke_from_app: string
}

export interface CaptureResult {
  platform: string
  granted: number
  write: number
  delete: number
  unused_write: number
  undocumented: number
}

// --- inventario ----------------------------------------------------------------------------------------------
export interface CredentialCopy {
  file: string
  git_ignored: boolean | null
}

export interface CredentialItem {
  platform: string
  /** Nombre de la variable de entorno o "archivo:campo". Nunca el valor. */
  name: string
  kind: CredentialKind
  kind_label: string
  location: CredentialLocation
  file: string
  present: boolean
  /** null = no se pudo saber (sin git). */
  git_ignored: boolean | null
  expires_at: string | null
  days_left: number | null
  expired: boolean
  in_app_backup: boolean
  copies: CredentialCopy[]
}

export interface PlatformCredentials {
  platform: string
  label: string
  present: boolean
  credentials: CredentialItem[]
}

export interface CredentialsResponse {
  platforms: PlatformCredentials[]
  backup_includes_credential_files: boolean
}

// --- rotación ------------------------------------------------------------------------------------------------
export interface RotationRow {
  platform: string
  label: string
  exposed_in_chat: boolean
  credentials_present: boolean
  rotated: boolean
  rotated_at: string | null
  rotated_by_email: string | null
  note: string | null
  portal_url: string
  steps: string[]
}

export interface RotationBoard {
  platforms: RotationRow[]
  pending_count: number
}

export interface RotationMarked {
  platform: string
  rotated: true
  rotated_at: string
  rotated_by_email: string | null
  note: string | null
}

// --- llamadas ------------------------------------------------------------------------------------------------
const BASE = '/api/security'
const JSON_HEADERS = { 'Content-Type': 'application/json' }
const rotationPath = (platform: string) => `${BASE}/rotation/${encodeURIComponent(platform)}`

export const security = {
  overview: () => authFetch(`${BASE}/overview`).then((r) => parse<SecurityOverview>(r)),
  scopes: () => authFetch(`${BASE}/scopes`).then((r) => parse<ScopesResponse>(r)),
  credentials: () => authFetch(`${BASE}/credentials`).then((r) => parse<CredentialsResponse>(r)),
  rotation: () => authFetch(`${BASE}/rotation`).then((r) => parse<RotationBoard>(r)),
}

/** Marca la plataforma como rotada (permiso settings.edit). Repetirlo no cambia nada. */
export function markRotated(platform: string, note: string | null) {
  return authFetch(rotationPath(platform), {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ note }),
  }).then((r) => parse<RotationMarked>(r))
}

/** Reabre la rotación: la plataforma vuelve a pendiente (permiso settings.edit). */
export function reopenRotation(platform: string) {
  return authFetch(rotationPath(platform), { method: 'DELETE' }).then((r) =>
    parse<{ platform: string; rotated: false; reopened: boolean }>(r),
  )
}

/** Renueva de forma segura el token del vendedor y guarda los NOMBRES de sus permisos. Devuelve solo conteos. */
export function captureSellerScopes() {
  return authFetch(`${BASE}/scopes/capture/seller`, { method: 'POST' }).then((r) => parse<CaptureResult>(r))
}

// --- pasos urgentes ------------------------------------------------------------------------------------------
export interface UrgentStep {
  id: string
  title: string
  detail: string
  tab: SecurityTabId
  tabLabel: string
}

export const URGENT_STEP_LIMIT = 3

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** Los pasos más urgentes, del más grave al menos grave: secretos sin ignorar, rotaciones, permisos sobrantes y vencimientos. */
export function urgentSteps(o: SecurityOverview): UrgentStep[] {
  const steps: UrgentStep[] = []
  if (o.files_not_git_ignored.length > 0) {
    steps.push({
      id: 'git',
      title: 'Haz que git ignore los archivos con secretos',
      detail: `${plural(o.files_not_git_ignored.length, 'archivo podría', 'archivos podrían')} subirse a un repositorio: ${o.files_not_git_ignored.join(', ')}.`,
      tab: 'credentials',
      tabLabel: 'Ver credenciales',
    })
  }
  if (o.pending_rotation_count > 0) {
    steps.push({
      id: 'rotation',
      title: 'Rota las credenciales expuestas',
      detail: `${plural(o.pending_rotation_count, 'plataforma sigue', 'plataformas siguen')} pendiente de rotar: ${o.pending_rotation.map(platformLabel).join(', ')}.`,
      tab: 'rotation',
      tabLabel: 'Ir a la rotación',
    })
  }
  if (o.unused_write_scopes > 0) {
    steps.push({
      id: 'scopes',
      title: 'Quita los permisos de escritura que la app no usa',
      detail: `${plural(o.unused_write_scopes, 'permiso', 'permisos')} en ${o.platforms_with_unused_write.map(platformLabel).join(', ')}. Se desmarcan en el portal de la plataforma.`,
      tab: 'scopes',
      tabLabel: 'Ver permisos',
    })
  }
  if (o.upcoming_expirations.length > 0) {
    const next = o.upcoming_expirations[0]
    steps.push({
      id: 'expiry',
      title: 'Reautoriza antes de que venza el acceso',
      detail: `${plural(o.upcoming_expirations.length, 'token vence', 'tokens vencen')} pronto o ya venció. El primero: ${next.name} (${platformLabel(next.platform)}).`,
      tab: 'credentials',
      tabLabel: 'Ver vencimientos',
    })
  }
  return steps.slice(0, URGENT_STEP_LIMIT)
}
