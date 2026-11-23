import { useState } from 'react'

import { Button } from '@/components/ui/button'

/**
 * Botón de descarga con estado: deshabilita mientras descarga, avisa el archivo guardado y muestra el
 * error. `onDownload` hace la descarga y devuelve el nombre del archivo (o lanza Error).
 */
export function DownloadButton({
  label,
  onDownload,
  disabled = false,
  variant = 'outline',
}: {
  label: string
  onDownload: () => Promise<string | void>
  disabled?: boolean
  variant?: 'default' | 'outline'
}) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    setDone(null)
    setError(null)
    try {
      setDone((await onDownload()) || 'Descargado')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo descargar')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <Button type="button" variant={variant} disabled={disabled || busy} onClick={run}>
        {busy ? 'Descargando…' : label}
      </Button>
      <div aria-live="polite" className="min-h-4 text-xs">
        {done && <span className="text-emerald-700">Guardado: {done}</span>}
        {error && (
          <span role="alert" className="text-red-700">
            {error}
          </span>
        )}
      </div>
    </div>
  )
}
