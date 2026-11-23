// Exportaciones para el contador (backend: app/export_api.py).
import { authFetch, parse, query, type Channel } from '@/lib/api'

export type ExportFormat = 'csv' | 'pdf'
export type ExportParam = 'start' | 'end' | 'channel' | 'account' | 'days'

export interface ExportItem {
  name: string
  title: string
  description: string
  /** Parámetros que acepta esta exportación. */
  params: ExportParam[]
  formats: ExportFormat[]
}

export interface ExportParams {
  start?: string
  end?: string
  channel?: Channel | ''
  account?: string
  days?: number
}

export const exportsApi = {
  catalog: () => authFetch('/api/export/catalog').then((r) => parse<ExportItem[]>(r)),
}

/** Nombre del archivo que propone el servidor (Content-Disposition), o `fallback`. */
function filenameOf(res: Response, fallback: string): string {
  const match = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') ?? '')
  return match?.[1] ?? fallback
}

/** Descarga una exportación y la guarda en el navegador. Lanza Error con el mensaje del servidor si falla. */
export async function downloadExport(name: string, format: ExportFormat, params: ExportParams): Promise<string> {
  const qs = query({
    start: params.start,
    end: params.end,
    channel: params.channel || undefined,
    account: params.account,
    days: params.days ? String(params.days) : undefined,
  })
  const res = await authFetch(`/api/export/${encodeURIComponent(name)}.${format}${qs}`)
  if (!res.ok) await parse<never>(res) // lanza ApiError con el detalle del backend
  const filename = filenameOf(res, `${name}.${format}`)
  const url = URL.createObjectURL(await res.blob())
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    URL.revokeObjectURL(url)
  }
  return filename
}
