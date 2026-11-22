import { api, type AlertsResponse } from '@/lib/api'

// Calcular las alertas toca varios reportes: se comparte una sola petición entre la barra lateral
// y la pantalla, y se reutiliza durante un minuto.
const TTL_MS = 60_000
let cache: { at: number; promise: Promise<AlertsResponse> } | null = null

export function loadAlerts(force = false): Promise<AlertsResponse> {
  if (!force && cache && Date.now() - cache.at < TTL_MS) return cache.promise
  const promise = api.alerts()
  cache = { at: Date.now(), promise }
  promise.catch(() => {
    if (cache?.promise === promise) cache = null
  })
  return promise
}
