import { useMemo, useState } from 'react'

import { ErrorAlert, Hint } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { redo } from '@/lib/redo'
import { useAsync } from '@/lib/useAsync'

const PAGE = 50

function csvCell(v: string | number | null | undefined): string {
  const s = v == null ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function RedoCharges() {
  const [subtype, setSubtype] = useState('')
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [page, setPage] = useState(0)

  const filters = useMemo(
    () => ({ subtype, q, start, end, limit: PAGE, offset: page * PAGE }),
    [subtype, q, start, end, page],
  )
  const data = useAsync(() => redo.charges(filters), [filters])
  const d = data.data
  const rows = d?.rows ?? []
  const total = d?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  const apply = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(0)
  }

  const download = () => {
    const header = ['Fecha', 'Orden', 'Tipo', 'Monto', 'Nota']
    const lines = [header, ...rows.map((r) => [r.created_at.slice(0, 10), r.order, r.label, r.amount.toFixed(2), r.note])]
    const body = lines.map((l) => l.map(csvCell).join(',')).join('\r\n')
    const blob = new Blob(['﻿' + body], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `redo-cargos-pagina-${page + 1}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Redo: libro de cargos</h1>
      <ErrorAlert message={data.error} />

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Tipo</span>
            <select
              className="border-input bg-background h-9 rounded-md border px-2 text-sm"
              value={subtype}
              onChange={(e) => apply(setSubtype)(e.target.value)}
            >
              <option value="">Todos</option>
              {(d?.subtypes ?? []).map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <form
            className="flex flex-col gap-1 text-sm"
            onSubmit={(e) => {
              e.preventDefault()
              apply(setQ)(text.trim())
            }}
          >
            <span className="text-muted-foreground text-xs">Orden o nota (Enter para buscar)</span>
            <Input
              className="h-9 w-56"
              value={text}
              placeholder="Ej. 5774…"
              onChange={(e) => setText(e.target.value)}
              onBlur={() => text.trim() !== q && apply(setQ)(text.trim())}
            />
          </form>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Desde</span>
            <Input type="date" className="h-9" value={start} onChange={(e) => apply(setStart)(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Hasta</span>
            <Input type="date" className="h-9" value={end} onChange={(e) => apply(setEnd)(e.target.value)} />
          </label>
          <Button
            variant="outline"
            onClick={() => {
              setSubtype('')
              setText('')
              setQ('')
              setStart('')
              setEnd('')
              setPage(0)
            }}
          >
            Limpiar
          </Button>
          <Button onClick={download} disabled={rows.length === 0} className="sm:ml-auto">
            Descargar lo visible (CSV)
          </Button>
        </CardContent>
      </Card>

      <div className="text-sm">
        <strong>{count(total)}</strong> cargos · total filtrado{' '}
        <strong className={(d?.amount ?? 0) < 0 ? 'text-green-700' : ''}>{money(d?.amount ?? 0)}</strong>
      </div>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Fecha</TableHead>
                <TableHead>Orden</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead className="text-right">Monto</TableHead>
                <TableHead>Nota</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'No hay cargos con estos filtros.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="whitespace-nowrap">{r.created_at.slice(0, 10)}</TableCell>
                  <TableCell className="font-mono text-xs">{r.order ?? '—'}</TableCell>
                  <TableCell>
                    {r.label}
                    {!r.is_cost && <span className="text-muted-foreground ml-1 text-xs">(no es costo)</span>}
                  </TableCell>
                  <TableCell className={`text-right whitespace-nowrap ${r.amount < 0 ? 'text-green-700' : ''}`}>
                    {money(r.amount)}
                  </TableCell>
                  <TableCell className="text-muted-foreground max-w-sm text-xs break-words whitespace-normal">
                    {r.note ?? ''}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-muted-foreground">
          Página {page + 1} de {pages}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" disabled={page === 0 || data.loading} onClick={() => setPage((p) => p - 1)}>
            Anterior
          </Button>
          <Button variant="outline" disabled={page + 1 >= pages || data.loading} onClick={() => setPage((p) => p + 1)}>
            Siguiente
          </Button>
        </div>
      </div>

      <Hint>
        Los créditos (anulaciones, reclamos pagados) aparecen en verde porque reducen tu gasto. El CSV incluye solo los
        50 cargos de la página que estás viendo.
      </Hint>
    </div>
  )
}
