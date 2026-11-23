import { useState } from 'react'

import { Callout, ErrorAlert, Hint, PILL, Pill } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api } from '@/lib/api'
import { count, money } from '@/lib/format'
import { skus, type DuplicateGroup, type MergeResult } from '@/lib/skus'
import { useAsync } from '@/lib/useAsync'

interface Pending {
  source: string
  target: string
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Confirmación explícita: la fusión no se puede deshacer, así que se explica qué se mueve. */
function ConfirmMerge({
  pending,
  onCancel,
  onDone,
}: {
  pending: Pending
  onCancel: () => void
  onDone: (result: MergeResult) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      onDone(await skus.merge(pending.source, pending.target, 'Fusión desde el Gestor de SKU'))
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }

  return (
    <div role="alertdialog" aria-label="Confirmar fusión" className="space-y-3 rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-900">
      <p className="font-semibold">
        ¿Fusionar <span className="font-mono">{pending.source}</span> dentro de{' '}
        <span className="font-mono">{pending.target}</span>?
      </p>
      <p>Esta acción es irreversible. Se hará lo siguiente:</p>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          Todas las ventas, listados por canal, gasto publicitario, devoluciones, muestras y afiliados de{' '}
          <span className="font-mono">{pending.source}</span> pasan a <span className="font-mono">{pending.target}</span>{' '}
          (si una orden tiene ambos SKU, se suman en una sola línea).
        </li>
        <li>Se conserva el costo de {pending.target}; del otro solo se copian las vigencias que no tenga.</li>
        <li>
          <span className="font-mono">{pending.source}</span> se elimina como producto y queda como alias: las próximas
          importaciones con ese SKU irán a <span className="font-mono">{pending.target}</span>.
        </li>
        <li>Se recalcula la utilidad histórica de todos los días (puede tardar unos segundos).</li>
      </ul>
      {error && <p className="font-medium">No se pudo fusionar: {error}</p>}
      <div className="flex gap-2">
        <Button variant="destructive" onClick={run} disabled={busy}>
          {busy ? 'Fusionando…' : 'Sí, fusionar definitivamente'}
        </Button>
        <Button variant="outline" onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
      </div>
    </div>
  )
}

function GroupCard({ group, onMerge }: { group: DuplicateGroup; onMerge: (p: Pending) => void }) {
  const [target, setTarget] = useState(group.suggested_sku)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <span className="font-mono">{group.members.map((m) => m.sku).join('  ·  ')}</span>
          <span className="text-muted-foreground text-sm font-normal">
            {money(group.total_sales)} en {count(group.total_units)} unidades
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Maestro</TableHead>
              <TableHead>SKU</TableHead>
              <TableHead>Nombre</TableHead>
              <TableHead className="text-right">Unidades</TableHead>
              <TableHead className="text-right">Ventas</TableHead>
              <TableHead>Canales</TableHead>
              <TableHead>Costo</TableHead>
              <TableHead className="text-right">Listados</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {group.members.map((m) => (
              <TableRow key={m.sku}>
                <TableCell>
                  <input
                    type="radio"
                    name={`target-${group.key}`}
                    aria-label={`Usar ${m.sku} como maestro`}
                    checked={target === m.sku}
                    onChange={() => setTarget(m.sku)}
                  />
                  {m.suggested && <Pill label="Sugerido" className={`ml-2 ${PILL.green}`} />}
                </TableCell>
                <TableCell className="font-mono text-xs">{m.sku}</TableCell>
                <TableCell className="max-w-xs truncate">{m.name ?? '—'}</TableCell>
                <TableCell className="text-right">{count(m.units)}</TableCell>
                <TableCell className="text-right">{money(m.sales)}</TableCell>
                <TableCell>{m.channels.join(', ') || '—'}</TableCell>
                <TableCell>
                  <Pill label={m.has_cost ? 'Con costo' : 'Sin costo'} className={m.has_cost ? PILL.green : PILL.amber} />
                </TableCell>
                <TableCell className="text-right">{count(m.listings)}</TableCell>
                <TableCell className="text-right">
                  {m.sku !== target && (
                    <Button size="sm" variant="outline" onClick={() => onMerge({ source: m.sku, target })}>
                      Fusionar en {target}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Hint>Elige con el círculo cuál SKU se conserva; los demás se fusionan en él.</Hint>
      </CardContent>
    </Card>
  )
}

/** Fusión manual: dos SKU buscados entre los productos existentes. */
function ManualMerge({ onMerge }: { onMerge: (p: Pending) => void }) {
  const products = useAsync(() => api.products(), [])
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const known = new Set((products.data ?? []).map((p) => p.sku))
  const problem =
    source && target && source === target
      ? 'El origen y el destino deben ser distintos.'
      : (source && !known.has(source)) || (target && !known.has(target))
        ? 'Elige SKU de la lista de productos existentes.'
        : null
  const ready = !!source && !!target && !problem

  return (
    <Card>
      <CardHeader>
        <CardTitle>Fusión manual</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <ErrorAlert message={products.error} />
        <datalist id="sku-options">
          {(products.data ?? []).map((p) => (
            <option key={p.id} value={p.sku}>
              {p.name ?? ''}
            </option>
          ))}
        </datalist>
        <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
          <label className="space-y-1 text-sm">
            <span className="font-medium">SKU que desaparece (origen)</span>
            <Input list="sku-options" value={source} onChange={(e) => setSource(e.target.value.trim())} placeholder="Busca por SKU" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">SKU que se conserva (destino)</span>
            <Input list="sku-options" value={target} onChange={(e) => setTarget(e.target.value.trim())} placeholder="Busca por SKU" />
          </label>
          <Button disabled={!ready} onClick={() => onMerge({ source, target })}>
            Fusionar…
          </Button>
        </div>
        {problem && <p className="text-sm text-red-700">{problem}</p>}
      </CardContent>
    </Card>
  )
}

function Aliases({ reload, onChanged }: { reload: number; onChanged: () => void }) {
  const aliases = useAsync(() => skus.aliases(), [reload])
  const products = useAsync(() => api.products(), [])
  const [alias, setAlias] = useState('')
  const [target, setTarget] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function add() {
    setError(null)
    try {
      await skus.addAlias(alias.trim(), target.trim())
      setAlias('')
      setTarget('')
      onChanged()
    } catch (e) {
      setError(errorText(e))
    }
  }

  async function remove(id: number) {
    setError(null)
    try {
      await skus.deleteAlias(id)
      onChanged()
    } catch (e) {
      setError(errorText(e))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Alias de SKU</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 overflow-x-auto">
        <Hint>
          Un alias es otro nombre de un producto: cuando una importación trae ese SKU, se asigna al producto maestro y no
          se crea uno nuevo. Quitar un alias no deshace fusiones ya hechas.
        </Hint>
        <ErrorAlert message={aliases.error} />
        {error && <Callout tone="danger">{error}</Callout>}
        <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
          <label className="space-y-1 text-sm">
            <span className="font-medium">Nuevo alias</span>
            <Input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="SKU alterno" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Producto maestro</span>
            <Input list="sku-options" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Busca por SKU" />
          </label>
          <Button disabled={!alias.trim() || !target.trim() || !products.data} onClick={add}>
            Agregar alias
          </Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Alias</TableHead>
              <TableHead>Producto maestro</TableHead>
              <TableHead>Nombre</TableHead>
              <TableHead>Motivo</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(aliases.data?.length ?? 0) === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                  {aliases.loading ? 'Cargando…' : 'Aún no hay alias. Se crean solos al fusionar SKU.'}
                </TableCell>
              </TableRow>
            )}
            {(aliases.data ?? []).map((a) => (
              <TableRow key={a.id}>
                <TableCell className="font-mono text-xs">{a.alias}</TableCell>
                <TableCell className="font-mono text-xs">{a.sku}</TableCell>
                <TableCell className="max-w-xs truncate">{a.product_name ?? '—'}</TableCell>
                <TableCell className="text-muted-foreground text-sm">{a.reason ?? '—'}</TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => remove(a.id)}>
                    Quitar
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

export function SkuManager() {
  const [reload, setReload] = useState(0)
  const [pending, setPending] = useState<Pending | null>(null)
  const [done, setDone] = useState<MergeResult | null>(null)
  const duplicates = useAsync(() => skus.duplicates(), [reload])
  const groups = duplicates.data?.groups ?? []

  function finished(result: MergeResult) {
    setPending(null)
    setDone(result)
    setReload((n) => n + 1)
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Gestor de SKU</h1>
      <Hint>
        Un mismo producto puede llegar con SKU distintos (&quot;XMAS8027&quot;, &quot;XMAS-8027&quot;, &quot;-1PK&quot;, sufijo E).
        Fusionarlos junta sus ventas, costos y listados para que la utilidad sea correcta.
      </Hint>

      {done && (
        <Callout tone="info">
          <strong>
            {done.source_sku} se fusionó en {done.target_sku}.
          </strong>{' '}
          {count(done.lines_moved)} líneas movidas, {count(done.lines_merged)} sumadas a una orden existente,{' '}
          {count(done.listings_moved)} listados y {count(done.ad_rows_moved)} registros de publicidad trasladados.
          Se recalcularon {count(done.gold_rows)} filas de utilidad.
        </Callout>
      )}

      {pending && <ConfirmMerge pending={pending} onCancel={() => setPending(null)} onDone={finished} />}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Duplicados sugeridos</h2>
        <ErrorAlert message={duplicates.error} />
        {duplicates.loading && !duplicates.data && <p className="text-muted-foreground text-sm">Cargando…</p>}
        {!duplicates.loading && !duplicates.error && groups.length === 0 && (
          <Callout tone="info">No se detectaron SKU duplicados.</Callout>
        )}
        {groups.map((g) => (
          <GroupCard key={g.key} group={g} onMerge={setPending} />
        ))}
      </section>

      <ManualMerge onMerge={setPending} />
      <Aliases reload={reload} onChanged={() => setReload((n) => n + 1)} />
    </div>
  )
}
