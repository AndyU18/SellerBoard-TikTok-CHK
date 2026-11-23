// Tipos y llamadas del resumen por correo/Slack/webhook (backend: app/notifications_api.py).
import { authFetch, parse } from '@/lib/api'

export type DigestPeriod = 'daily' | 'weekly'
export type NotificationChannel = 'email' | 'slack' | 'webhook'
export type NotificationStatus = 'sent' | 'failed' | 'skipped'

export interface NotificationLogRow {
  id: number
  kind: 'daily' | 'weekly' | 'test'
  channel: NotificationChannel
  status: NotificationStatus
  detail: string | null
  created_at: string
}

export interface NotificationsStatus {
  enabled: boolean
  daily_hour: number
  weekly_weekday: number // 0 = lunes
  timezone: string
  channels: Record<NotificationChannel, boolean>
  email_recipients: number
  log: NotificationLogRow[]
}

export interface DigestPreview {
  text: string
  html: string
  digest: { period: DigestPeriod; label: string; range: { start: string; end: string } }
}

export interface TestResult {
  results: Record<string, { status: NotificationStatus; detail: string }>
}

export const notificationsApi = {
  status: () => authFetch('/api/notifications/status').then((r) => parse<NotificationsStatus>(r)),
  preview: (period: DigestPeriod) =>
    authFetch(`/api/digest/preview?period=${period}`).then((r) => parse<DigestPreview>(r)),
  sendTest: () => authFetch('/api/notifications/test', { method: 'POST' }).then((r) => parse<TestResult>(r)),
}
