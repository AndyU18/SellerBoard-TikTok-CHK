import { Fragment, useState, type FormEvent, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { assistantApi, type ExplainComponent, type ExplainProduct, type WeeklyReport } from '@/lib/assistant'
import { money, percent } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const PERIODS = [7, 14, 30]
const SUGGESTIONS = [
  '¿Por qué bajó la utilidad esta semana?',
  '¿Qué productos pierden dinero y qué debería hacer con ellos?',
  '¿Qué debo reponer primero?',
  '¿Cómo va mi flujo de caja?',
]
const PRIVACY =
  'Tus cifras (ventas, costos, SKU) se envían a Anthropic para responder; no se envían datos de clientes.'

const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${money(Math.abs(v))}`
const toneOf = (v: number) => (v < 0 ? 'text-red-600 dark:text-red-400' : v > 0 ? 'text-green-600 dark:text-green-400' : '')

/** Texto del modelo con saltos de línea, viñetas "- " y **negritas**; sin HTML crudo. */
function RichText({ text }: { text: string }) {
  const inline = (line: string): ReactNode =>
    line.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith('**') && part.endsWith('**') && part.length > 4 ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : (
        <Fragment key={i}>{part}</Fragment>
      ),
    )
  return (
    <div className="space-y-1.5 text-sm leading-relaxed">
      {text.split('\n').map((raw, i) => {
        const line = raw.trimEnd()
        if (!line.trim()) return <div key={i} className="h-1" />
        const bullet = /^\s*[-*•]\s+(.*)$/.exec(line)
        return bullet ? (
          <div key={i} className="flex gap-2 pl-2">
            <span aria-hidden>•</span>
            <span>{inline(bullet[1])}</span>
          </div>
        ) : (
          <p key={i}>{inline(line)}</p>
        )
      })}
    </div>
  )
}

/** Barras divergentes con CSS: roja a la izquierda (resta utilidad), verde a la derecha (suma). */
function EffectBars({ rows }: { rows: { key: string; label: string; sub?: string; value: number }[] }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 0.01)
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.key} className="grid grid-cols-[minmax(0,9rem)_1fr_5.5rem] items-center gap-2 text-sm sm:grid-cols-[minmax(0,12rem)_1fr_6rem]">
          <span className="truncate" title={r.label}>
            {r.label}
            {r.sub && <span className="block truncate text-xs text-muted-foreground">{r.sub}</span>}
          </span>
          <span className="relative h-3 rounded bg-muted" aria-hidden>
            <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
            <span
              className={`absolute inset-y-0 rounded ${r.value < 0 ? 'right-1/2 bg-red-500' : 'left-1/2 bg-green-500'}`}
              style={{ width: `${(Math.abs(r.value) / max) * 50}%` }}
            />
          </span>
          <span className={`text-right tabular-nums ${toneOf(r.value)}`}>{signed(r.value)}</span>
        </li>
      ))}
    </ul>
  )
}

const productLabel = (p: ExplainProduct) => p.product_name || p.sku

function ExplainCard() {
  const [days, setDays] = useState(7)
  const explain = useAsync(() => assistantApi.explain(days), [days])
  const data = explain.data
  const components: ExplainComponent[] = (data?.components ?? []).filter((c) => Math.abs(c.effect) >= 0.005)

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>¿Por qué cambió la utilidad?</CardTitle>
        <div className="flex gap-1" role="group" aria-label="Periodo a comparar">
          {PERIODS.map((d) => (
            <Button key={d} size="sm" variant={d === days ? 'default' : 'outline'} onClick={() => setDays(d)}>
              {d} días
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {explain.error && <p className="text-sm text-red-600">{explain.error}</p>}
        {explain.loading && !data && <p className="text-sm text-muted-foreground">Calculando…</p>}
        {data && (
          <>
            <div className="space-y-1">
              <p className="text-base font-medium">{data.headline}</p>
              <p className="text-xs text-muted-foreground">
                {data.current.start} a {data.current.end} contra {data.previous.start} a {data.previous.end}. Cálculo
                automático con tus datos, sin IA.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <Stat label="Utilidad actual" value={money(data.change.profit_current)} />
              <Stat label="Utilidad previa" value={money(data.change.profit_previous)} />
              <Stat
                label="Cambio"
                value={signed(data.change.profit_delta)}
                hint={data.change.profit_pct == null ? undefined : percent(data.change.profit_pct)}
                tone={toneOf(data.change.profit_delta)}
              />
            </div>

            {components.length > 0 && (
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Efecto de cada componente en la utilidad</h3>
                <EffectBars rows={components.map((c) => ({ key: c.key, label: c.label, value: c.effect }))} />
              </section>
            )}

            {data.products.length > 0 && (
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Productos que más explican el cambio</h3>
                <EffectBars
                  rows={data.products.map((p) => ({
                    key: p.sku,
                    label: productLabel(p),
                    sub: `Volumen ${signed(p.volume_effect)} · Tasa ${signed(p.rate_effect)}`,
                    value: p.delta,
                  }))}
                />
                <p className="text-xs text-muted-foreground">
                  Volumen: cambio en unidades vendidas a la utilidad unitaria previa. Tasa: cambio en la utilidad por
                  unidad de lo que se vendió ahora.
                </p>
              </section>
            )}

            {data.channels.length > 1 && (
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Por canal</h3>
                <EffectBars rows={data.channels.map((c) => ({ key: c.channel, label: c.channel, value: c.delta }))} />
              </section>
            )}

            {data.caveats.length > 0 && (
              <ul className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                {data.caveats.map((c) => (
                  <li key={c}>• {c}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Stat({ label, value, hint, tone = '' }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tone}`}>
        {value} {hint && <span className="text-sm font-normal">({hint})</span>}
      </div>
    </div>
  )
}

interface Turn {
  question: string
  answer?: string
  error?: string
}

function Chat({ configured, loadingStatus }: { configured: boolean; loadingStatus: boolean }) {
  const [days, setDays] = useState(14)
  const [question, setQuestion] = useState('')
  const [turn, setTurn] = useState<Turn | null>(null)
  const [busy, setBusy] = useState(false)
  const disabled = !configured || busy

  async function send(text: string) {
    const q = text.trim()
    if (q.length < 3 || disabled) return
    setBusy(true)
    setTurn({ question: q })
    try {
      const res = await assistantApi.ask(q, days)
      setTurn({ question: q, answer: res.answer })
      setQuestion('')
    } catch (e) {
      setTurn({ question: q, error: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(question)
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>Pregúntale al asistente</CardTitle>
        <label className="flex items-center gap-2 text-sm">
          Datos de los últimos
          <select
            className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
            value={days}
            disabled={disabled}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {[7, 14, 30, 60, 90].map((d) => (
              <option key={d} value={d}>
                {d} días
              </option>
            ))}
          </select>
        </label>
      </CardHeader>
      <CardContent className="space-y-4">
        {!configured && !loadingStatus && (
          <div className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <p className="font-medium">El asistente con IA está apagado.</p>
            <p>
              Para activarlo, agrega <code>ANTHROPIC_API_KEY</code> (o <code>CHK_AI_API_KEY</code>) en{' '}
              <code>backend/.env</code> y reinicia el backend. Mientras tanto, la explicación de arriba y el informe
              automático siguen funcionando sin enviar nada fuera.
            </p>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <Button key={s} size="sm" variant="outline" disabled={disabled} onClick={() => void send(s)}>
              {s}
            </Button>
          ))}
        </div>

        <form onSubmit={onSubmit} className="flex gap-2">
          <Input
            value={question}
            maxLength={800}
            disabled={disabled}
            placeholder="Escribe tu pregunta sobre ventas, utilidad, inventario o caja…"
            aria-label="Pregunta para el asistente"
            onChange={(e) => setQuestion(e.target.value)}
          />
          <Button type="submit" disabled={disabled || question.trim().length < 3}>
            Preguntar
          </Button>
        </form>

        {turn && (
          <div className="space-y-2" aria-live="polite">
            <p className="text-sm font-medium">Tú: {turn.question}</p>
            <div className="rounded-md border bg-muted/40 p-3">
              {busy && <p className="text-sm text-muted-foreground">Pensando…</p>}
              {turn.error && <p className="text-sm text-red-600">{turn.error}</p>}
              {turn.answer && <RichText text={turn.answer} />}
            </div>
          </div>
        )}

        <p className="text-xs text-muted-foreground">{PRIVACY} Las respuestas pueden contener errores: verifica las cifras importantes.</p>
      </CardContent>
    </Card>
  )
}

function WeeklyCard({ configured }: { configured: boolean }) {
  const [report, setReport] = useState<WeeklyReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function generate() {
    setBusy(true)
    setError(null)
    try {
      setReport(await assistantApi.weeklyReport())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>Informe semanal</CardTitle>
        <Button size="sm" onClick={() => void generate()} disabled={busy}>
          {busy ? 'Generando…' : 'Generar informe semanal'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {!report && !error && (
          <p className="text-sm text-muted-foreground">
            {configured
              ? 'Redacta con IA un resumen de la semana. Tus cifras (sin datos de clientes) se envían a Anthropic.'
              : 'Sin clave de IA se genera un informe automático con reglas, sin enviar nada fuera.'}
          </p>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}
        {report && (
          <>
            <p className="text-xs text-muted-foreground">
              {report.ai ? `Redactado por IA (${report.model ?? 'modelo'}).` : 'Informe automático, sin IA.'}
            </p>
            <RichText text={report.report} />
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function Assistant() {
  const status = useAsync(() => assistantApi.status(), [])
  const configured = status.data?.configured ?? false

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Asistente</h1>
        <p className="text-sm text-muted-foreground">
          La explicación de la utilidad es un cálculo de tus datos; las preguntas y el informe narrado usan IA.
        </p>
      </div>
      <ExplainCard />
      <Chat configured={configured} loadingStatus={status.loading} />
      <WeeklyCard configured={configured} />
    </div>
  )
}
