import { Upload } from 'lucide-react'
import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { api, ApiError, type ImportResult } from '@/lib/api'

interface UploaderProps {
  kind: 'orders' | 'ad-spend' | 'cogs' | 'shipping-labels'
  title: string
  description: string
  columns: string
}

function Uploader({ kind, title, description, columns }: UploaderProps) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [problems, setProblems] = useState<string[]>([])

  async function submit() {
    const file = input.current?.files?.[0]
    if (!file) return
    setBusy(true)
    setResult(null)
    setProblems([])
    try {
      setResult(await api.upload(kind, file))
      if (input.current) input.current.value = ''
    } catch (e) {
      setProblems(e instanceof ApiError && e.problems.length ? e.problems : [String(e)])
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-xs">
          Columnas: <code>{columns}</code>
        </p>
        <input
          ref={input}
          type="file"
          accept=".csv,.xlsx,.xls"
          aria-label={`Archivo de ${title}`}
          className="block w-full text-sm file:mr-3 file:rounded-md file:border file:bg-transparent file:px-3 file:py-1.5"
        />
        <Button onClick={submit} disabled={busy}>
          <Upload className="size-4" />
          {busy ? 'Importando…' : 'Importar'}
        </Button>
        {result && (
          <p role="status" className="text-sm text-green-700">
            Listo: {result.inserted} nuevas, {result.updated} actualizadas
            {result.accounts_created > 0 && `, ${result.accounts_created} cuentas creadas`}
            {result.products_created > 0 && `, ${result.products_created} productos creados`}.
            {result.matched_orders !== undefined && (
              <>
                {' '}
                {result.matched_orders} órdenes cruzadas con tus ventas; {result.unmatched_orders} aún sin orden en la
                base (no sincronizadas o de otro canal).
                {result.stats && result.stats.unassigned_charges > 0 &&
                  ` ${result.stats.unassigned_charges} cargos sin orden ($${result.stats.unassigned_total}) no se atribuyen a un producto.`}
              </>
            )}
          </p>
        )}
        {problems.length > 0 && (
          <ul role="alert" className="list-disc space-y-1 pl-5 text-sm text-red-700">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function TikTokSync() {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function run() {
    setBusy(true)
    setMessage('')
    setError('')
    try {
      const r = await api.syncTikTok(30)
      setMessage(
        `Listo: ${r.stats.orders_settled} órdenes liquidadas (${r.imported.inserted} nuevas, ` +
          `${r.imported.updated} actualizadas); ${r.stats.orders_pending_settlement} pendientes de liquidar.`,
      )
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>TikTok Shop</CardTitle>
        <CardDescription>Trae directo de la API las órdenes liquidadas de los últimos 30 días.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button onClick={run} disabled={busy}>
          {busy ? 'Sincronizando…' : 'Sincronizar ahora'}
        </Button>
        {message && (
          <p role="status" className="text-sm text-green-700">
            {message}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function RedoSync() {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function run() {
    setBusy(true)
    setMessage('')
    setError('')
    try {
      const r = await api.syncRedo()
      setMessage(
        `Listo: ${r.inserted} órdenes nuevas, ${r.updated} actualizadas. ${r.matched_orders} cruzadas con tus ventas; ` +
          `${r.unmatched_orders} aún sin orden en la base.` +
          (r.stats && r.stats.unassigned_charges > 0
            ? ` ${r.stats.unassigned_charges} cargos sin orden ($${r.stats.unassigned_total}) no se atribuyen a un producto.`
            : ''),
      )
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Redo (etiquetas)</CardTitle>
        <CardDescription>
          Trae directo de Redo el costo real de cada etiqueta (solo lectura). Reemplaza subir el CSV.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button onClick={run} disabled={busy}>
          {busy ? 'Sincronizando…' : 'Sincronizar ahora'}
        </Button>
        {message && (
          <p role="status" className="text-sm text-green-700">
            {message}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function WalmartSync() {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function run() {
    setBusy(true)
    setMessage('')
    setError('')
    try {
      const r = await api.syncWalmart(30)
      setMessage(
        `Listo: ${r.stats.orders_seen} órdenes (${r.imported.inserted} nuevas, ${r.imported.updated} actualizadas). ` +
          `Comisión real en ${r.stats.fees_actual} líneas; estimada al 15% en ${r.stats.fees_estimated} hasta que Walmart las liquide.`,
      )
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Walmart</CardTitle>
        <CardDescription>
          Trae de la API las órdenes de los últimos 30 días y las liquidaciones (comisión real y etiquetas).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button onClick={run} disabled={busy}>
          {busy ? 'Sincronizando…' : 'Sincronizar ahora'}
        </Button>
        {message && (
          <p role="status" className="text-sm text-green-700">
            {message}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function Import() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Importar</h1>
      <p className="text-muted-foreground max-w-2xl text-sm">
        Sube reportes en CSV o Excel. Volver a subir el mismo archivo actualiza los datos sin duplicarlos.
        Los canales válidos son amazon, tiktok_shop, walmart y shopify.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <Uploader
          kind="orders"
          title="Órdenes"
          description="Ventas, comisiones, envío y reembolsos por orden y SKU."
          columns="date, channel, account, order_id, sku, units, sales, fees, shipping, refunds"
        />
        <Uploader
          kind="ad-spend"
          title="Publicidad"
          description="Gasto diario de Amazon Ads, TikTok Ads o Walmart Connect."
          columns="date, channel, account, sku (opcional), spend"
        />
        <Uploader
          kind="cogs"
          title="Costos (COGS)"
          description="Costo unitario por SKU. Actualiza los existentes y crea los que falten."
          columns="sku, cogs_unit (o cost), product_name (opcional)"
        />
        <Uploader
          kind="shipping-labels"
          title="Etiquetas de envío (Redo)"
          description="Costo real de cada etiqueta, cruzado por número de orden y repartido entre los productos de la orden."
          columns="billing-transactions.csv de Redo: Created at, Type, Category, Order, Amount"
        />
        <TikTokSync />
        <WalmartSync />
        <RedoSync />
      </div>
    </div>
  )
}
