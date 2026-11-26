import { useMemo } from 'react'

import { Callout, ErrorAlert, Kpi } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count, money } from '@/lib/format'
import { tiktokService, type CsAgents, type CsOfficialTotals, type CsOverview } from '@/lib/tiktokService'
import { useAsync } from '@/lib/useAsync'

import { NO_VALUE, shortDate } from './tiktokFormat'
import {
  categoryLabel,
  durationText,
  hoursText,
  pctText,
  secondsToMinutes,
  type CsTabId,
  useWindowDays,
  waitingCountTone,
} from './tiktokServiceFormat'
import { CoverageNotices, OfficialBadge, ServiceHeader } from './tiktokServiceUi'

const SHORTCUTS: { id: CsTabId; title: string; description: string }[] = [
  { id: 'csConversations', title: 'Conversaciones', description: 'Lista con estado, categoría y espera.' },
  { id: 'csResponseTimes', title: 'Tiempos de respuesta', description: 'Rangos, horas del día y mejores franjas.' },
  { id: 'csCategories', title: 'Categorías', description: 'Qué preguntan y cuánto tardamos.' },
  { id: 'csBacklog', title: 'Pendientes', description: 'Lo que sigue sin responder, por antigüedad.' },
]

const SERIES: ComboSeries[] = [
  { key: 'new_conversations', label: 'Conversaciones nuevas', type: 'bar', color: COLOR.blue },
  { key: 'buyer_messages', label: 'Mensajes de compradores', type: 'bar', color: COLOR.amber },
  { key: 'seller_messages', label: 'Mensajes del vendedor', type: 'bar', color: COLOR.good },
  {
    key: 'median_response_minutes',
    label: 'Respuesta mediana (min)',
    type: 'line',
    axis: 'right',
    color: COLOR.bad,
    format: (v) => `${v.toFixed(1)} min`,
  },
]

const sharePct = (part: number, total: number) => (total ? `${((part / total) * 100).toFixed(0)}% del total` : undefined)

/** Tarjeta con título y la insignia de dato oficial: se distingue de las cifras que calcula la app. */
function OfficialTile({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-muted-foreground text-xs">{title}</div>
      <div className="text-xl font-semibold">{value}</div>
      {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
    </div>
  )
}

function OfficialMetrics({ official }: { official: CsOfficialTotals }) {
  const hasData = official.days_with_data > 0
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle className="text-base">Métricas oficiales de TikTok</CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">
            {hasData ? `Calculadas por TikTok en ${count(official.days_with_data)} días con sesiones; no son cifras de la app.` : 'TikTok aún no entrega métricas para este período.'}
          </p>
        </div>
        <OfficialBadge />
      </CardHeader>
      {hasData && (
        <CardContent className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <OfficialTile title="Sesiones de servicio" value={count(official.sessions)} />
          <OfficialTile title="% de respuesta" value={pctText(official.response_percentage)} />
          <OfficialTile title="Tiempo de respuesta" value={durationText(official.response_seconds)} />
          <OfficialTile title="% de satisfacción" value={pctText(official.satisfaction_percentage)} />
          <OfficialTile title="Conversión" value={pctText(official.conversion_percentage)} />
          <OfficialTile title="GMV guiado por servicio" value={money(official.guided_gmv)} />
        </CardContent>
      )}
    </Card>
  )
}

function AgentCard({ agents, loading }: { agents: CsAgents | null; loading: boolean }) {
  const value = !agents ? (loading ? 'Cargando…' : NO_VALUE) : agents.status === 'accepting_chats' ? 'Aceptan chats' : agents.status === 'not_accepting_chats' ? 'No aceptan chats' : 'Sin información'
  const tone = agents?.status === 'accepting_chats' ? 'text-green-700' : agents?.status === 'not_accepting_chats' ? 'text-red-700' : ''
  return (
    <Kpi
      title="Estado de los agentes"
      value={value}
      className={tone}
      hint={agents?.status === 'not_accepting_chats' ? 'TikTok no está asignando chats nuevos.' : 'Configuración de TikTok (solo lectura).'}
    />
  )
}

function Indicators({ overview, agents, agentsLoading }: { overview: CsOverview; agents: CsAgents | null; agentsLoading: boolean }) {
  const { response } = overview
  const oldest = agents?.oldest_wait_hours
  return (
    <>
      <section aria-label="Estado actual" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Conversaciones abiertas" value={count(overview.open_conversations)} hint={`${count(overview.waiting_buyer)} esperan al comprador`} />
        <Kpi
          title="Esperan respuesta del vendedor"
          value={count(overview.waiting_seller)}
          className={waitingCountTone(overview.waiting_seller)}
          hint={oldest != null ? `La más antigua lleva ${hoursText(oldest)}` : undefined}
        />
        <Kpi title="Mensajes no leídos" value={count(overview.unread_total)} />
        <AgentCard agents={agents} loading={agentsLoading} />
      </section>

      <section aria-label="Respuesta" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Respuesta media" value={durationText(response.avg_seconds)} hint={`Mediana ${durationText(response.median_seconds)} · p90 ${durationText(response.p90_seconds)}`} />
        <Kpi title="Respondidas en 1 h" value={pctText(response.within_1h_pct)} hint="Sobre conversaciones con mensaje de comprador" />
        <Kpi title="Respondidas en 24 h" value={pctText(response.within_24h_pct)} hint={`Tasa de respuesta ${pctText(response.response_rate_pct)}`} />
        <Kpi
          title="Con mensaje de comprador"
          value={count(response.conversations_with_buyer_message)}
          hint={`${count(response.responded)} con respuesta humana · ${count(overview.bot_only_conversations)} solo bot o sistema (${sharePct(overview.bot_only_conversations, overview.conversations_in_window) ?? '—'})`}
        />
      </section>
    </>
  )
}

function Shortcuts({ onOpenTab }: { onOpenTab?: (id: CsTabId) => void }) {
  return (
    <nav aria-label="Más análisis de servicio al cliente" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {SHORTCUTS.map((s) => (
        <button
          key={s.id}
          type="button"
          onClick={() => onOpenTab?.(s.id)}
          className="rounded-lg border p-4 text-left hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
        >
          <div className="font-medium">{s.title} →</div>
          <div className="text-muted-foreground text-xs">{s.description}</div>
        </button>
      ))}
    </nav>
  )
}

/** Panorama del servicio al cliente de TikTok Shop: pendientes, tiempos de respuesta, volumen y métricas oficiales. */
export function TikTokCsOverview({ onOpenTab }: { onOpenTab?: (id: CsTabId) => void }) {
  const days = useWindowDays()
  const overviewData = useAsync(() => tiktokService.overview(days), [days])
  const agentsData = useAsync(() => tiktokService.agents(), [])
  const overview = overviewData.data

  const volume = useMemo(
    () => (overview?.volume_by_day ?? []).map((d) => ({ ...d, median_response_minutes: secondsToMinutes(d.median_response_seconds) })),
    [overview],
  )
  const slices = useMemo<DonutSlice[]>(
    () => (overview?.categories ?? []).map((c) => ({ id: c.category, label: categoryLabel(c.category), value: c.conversations })),
    [overview],
  )

  const loading = overviewData.loading && !overview

  return (
    <div className="space-y-6 p-6">
      <ServiceHeader
        title="Servicio al cliente de TikTok Shop"
        note={`Últimos ${days} días (las cifras de "abiertas" y "pendientes" son de hoy). Cuenta como atención la respuesta humana del vendedor; el bot automático de TikTok no resuelve dudas.`}
      />

      <ErrorAlert message={overviewData.error} />
      {loading && <EmptyChart>Cargando…</EmptyChart>}
      {overview && !overview.available && <Callout tone="info">Todavía no hay conversaciones sincronizadas.</Callout>}

      {overview?.available && (
        <>
          <CoverageNotices sync={overview.sync} windowDays={days} firstDay={overview.volume_by_day[0]?.date ?? null} generatedAt={overview.generated_at} />
          <Indicators overview={overview} agents={agentsData.data} agentsLoading={agentsData.loading} />
          <OfficialMetrics official={overview.official} />

          <div className="grid gap-4 xl:grid-cols-3">
            <ChartCard
              className="xl:col-span-2"
              title="Volumen por día"
              subtitle="Conversaciones nuevas y mensajes por quién escribe, con la respuesta mediana del día."
              csv={volume.map((d) => ({
                fecha: d.date,
                conversaciones_nuevas: d.new_conversations,
                mensajes_compradores: d.buyer_messages,
                mensajes_vendedor: d.seller_messages,
                respuesta_mediana_min: d.median_response_minutes,
                pendientes_al_cierre: d.pending_at_close,
              }))}
              filename="tiktok-servicio-volumen-por-dia"
            >
              <ComboChart
                data={volume}
                xKey="date"
                series={SERIES}
                leftFormat={axisNumber}
                rightFormat={(v) => `${v} min`}
                xFormat={(v) => shortDate(String(v))}
                height={300}
              />
            </ChartCard>

            <ChartCard
              title="Conversaciones por categoría"
              subtitle="Solo conversaciones con mensaje de comprador."
              csv={slices.map((s) => ({ categoria: s.label, conversaciones: s.value }))}
              filename="tiktok-servicio-categorias"
            >
              <Donut slices={slices} format={count} centerLabel="conversaciones" height={220} />
            </ChartCard>
          </div>

          <Shortcuts onOpenTab={onOpenTab} />
        </>
      )}
    </div>
  )
}
