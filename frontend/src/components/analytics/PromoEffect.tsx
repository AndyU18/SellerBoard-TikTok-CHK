import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { ErrorAlert, Hint, Kpi, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics, type PromoVerdict } from '@/lib/analytics'
import { count, money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const VERDICT: Record<PromoVerdict, { label: string; pill: string }> = {
  worth_it: { label: 'Vale la pena', pill: PILL.green },
  not_worth: { label: 'No compensa', pill: PILL.red },
  insufficient: { label: 'Pocos días', pill: PILL.slate },
}

/** ¿Las promociones aumentaron las ventas y la utilidad? */
export function PromoEffect() {
  const scope = useScope()
  const [verdict, setVerdict] = useState<PromoVerdict | 'all'>('all')
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => analytics.promoEffect(sc, 90), [sc])
  const d = data.data
  const rows = (d?.rows ?? []).filter((r) => verdict === 'all' || r.verdict === verdict)

  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">¿Las promociones funcionan? (90 días)</h2>
      <ErrorAlert message={data.error} />
      <div className="grid gap-4 md:grid-cols-3">
        <Kpi
          title="Vale la pena"
          value={count(d?.summary.worth_it ?? 0)}
          hint="más utilidad por día con promoción"
          className="text-green-700"
        />
        <Kpi
          title="No compensa"
          value={count(d?.summary.not_worth ?? 0)}
          hint="menos utilidad por día con promoción"
          className={(d?.summary.not_worth ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi
          title="Sin veredicto"
          value={count(d?.summary.insufficient ?? 0)}
          hint="menos de 3 días con o sin promoción"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Por profundidad del descuento</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Canal</TableHead>
                <TableHead>Descuento</TableHead>
                <TableHead className="text-right">Días-producto</TableHead>
                <TableHead className="text-right">Índice de unidades</TableHead>
                <TableHead className="text-right">Utilidad por día-producto</TableHead>
                <TableHead className="text-right">Margen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(d?.buckets ?? []).map((b) => (
                <TableRow key={`${b.channel}-${b.bucket}`}>
                  <TableCell>
                    <ChannelBadge channel={b.channel} />
                  </TableCell>
                  <TableCell>{b.bucket}</TableCell>
                  <TableCell className="text-right">{count(b.sku_days)}</TableCell>
                  <TableCell className="text-right">{b.units_index.toFixed(2)}×</TableCell>
                  <TableCell className={`text-right ${tone(b.profit_per_sku_day)}`}>
                    {money(b.profit_per_sku_day)}
                  </TableCell>
                  <TableCell className={`text-right ${tone(b.margin)}`}>{pct1(b.margin)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Segmented
        label="Veredicto"
        value={verdict}
        options={[
          { value: 'all', label: 'Todos' },
          { value: 'not_worth', label: 'No compensa', count: d?.summary.not_worth },
          { value: 'worth_it', label: 'Vale la pena', count: d?.summary.worth_it },
          { value: 'insufficient', label: 'Pocos días', count: d?.summary.insufficient },
        ]}
        onChange={setVerdict}
      />
      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Días promo / normal</TableHead>
                <TableHead className="text-right">Descuento prom.</TableHead>
                <TableHead className="text-right">Unid./día promo → normal</TableHead>
                <TableHead className="text-right">Más ventas</TableHead>
                <TableHead className="text-right">Utilidad/día promo → normal</TableHead>
                <TableHead>Veredicto</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground py-8 text-center">
                    {data.loading ? 'Cargando…' : 'Sin productos con este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.slice(0, 40).map((r) => (
                <TableRow key={`${r.channel}-${r.sku}`}>
                  <TableCell>
                    <div className="flex min-w-44 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground flex items-center gap-2 text-xs">
                        {r.sku} <ChannelBadge channel={r.channel} />
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    {r.promo_days} / {r.normal_days}
                  </TableCell>
                  <TableCell className="text-right">{pct1(r.avg_depth)}</TableCell>
                  <TableCell className="text-right">
                    {r.units_per_day_promo.toFixed(1)} → {r.units_per_day_normal.toFixed(1)}
                  </TableCell>
                  <TableCell className={`text-right ${tone(r.uplift)}`}>{pct1(r.uplift)}</TableCell>
                  <TableCell className="text-right">
                    {money(r.profit_per_day_promo)} → {money(r.profit_per_day_normal)}
                  </TableCell>
                  <TableCell>
                    <Pill label={VERDICT[r.verdict].label} className={VERDICT[r.verdict].pill} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Hint>
        Un día cuenta como “con promoción” si descontaste {pct1(d?.promo_threshold)} o más del precio de lista. Se
        compara la utilidad por día con y sin promoción de cada producto (los días sin ventas cuentan como cero). El
        índice de unidades es cuántas unidades se venden ese día frente al promedio normal del mismo producto. Es una
        comparación observacional: no separa el efecto del descuento del de la temporada, la publicidad o los
        creadores.
      </Hint>
    </section>
  )
}
