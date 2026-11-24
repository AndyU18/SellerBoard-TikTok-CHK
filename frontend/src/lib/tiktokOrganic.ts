import { authFetch, parse } from '@/lib/api'

/** Estado de la conexión con la cuenta orgánica de TikTok (GET /api/tiktok-organic/status). Nunca incluye tokens. */
export interface OrganicStatus {
  configured: boolean
  redirect_uri: string | null
  connected: boolean
  needs_reauthorization: boolean
  scopes: string[]
  missing_scopes: string[]
  access_expires_at: string | null
  refresh_expires_at: string | null
  refresh_days_left: number | null
  pending_steps: string[]
  username: string | null
  last_snapshot_date: string | null
  token_file_error: string | null
}

export interface OrganicAuthorizeUrl {
  authorize_url: string
  redirect_uri: string
  state: string
  scopes: string[]
  expires_in_seconds: number
}

/** Lo que TikTok manda de vuelta a la URL de retorno: `?code=...&state=...` (o `?error=...`). */
export interface OrganicCallback {
  code: string | null
  state: string | null
  error: string | null
}

const BASE = '/api/tiktok-organic'

const send = <T>(path: string, body?: unknown) =>
  authFetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => parse<T>(r))

export const tiktokOrganic = {
  status: () => authFetch(`${BASE}/status`).then((r) => parse<OrganicStatus>(r)),
  authorizeUrl: () => authFetch(`${BASE}/authorize-url`).then((r) => parse<OrganicAuthorizeUrl>(r)),
  connect: (code: string, state: string) => send<OrganicStatus>('/connect', { code, state }),
  disconnect: () => send<{ disconnected: boolean; had_tokens: boolean }>('/disconnect'),
}

/**
 * Saca `code` y `state` de lo que el usuario pegue: la URL completa a la que TikTok lo redirigió
 * (lo normal) o directamente la parte `?code=...&state=...`. Devuelve null si no encuentra nada útil.
 */
export function parseCallback(raw: string): OrganicCallback | null {
  const text = raw.trim()
  if (!text) return null
  let params: URLSearchParams
  try {
    params = new URL(text).searchParams
  } catch {
    params = new URLSearchParams(text.replace(/^[^?]*\?/, '').replace(/^\?/, ''))
  }
  const code = params.get('code')
  const state = params.get('state')
  const error = params.get('error_description') ?? params.get('error')
  return code || error ? { code, state, error } : null
}
