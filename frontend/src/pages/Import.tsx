import { Upload } from 'lucide-react'
import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { api, ApiError, type ImportResult } from '@/lib/api'

interface UploaderProps {
  kind: 'orders' | 'ad-spend'
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

export function Import() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Importar</h1>
      <p className="text-muted-foreground max-w-2xl text-sm">
        Sube reportes en CSV o Excel. Volver a subir el mismo archivo actualiza los datos sin duplicarlos.
        Los canales válidos son amazon, tiktok_shop y walmart.
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
      </div>
    </div>
  )
}
