import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, NumberField, Pill } from '@/components/analytics/ui'
import type { CsvRow } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import {
  MAX_LIST_LIMIT,
  tiktokService,
  type CsCategory,
  type CsConversation,
  type CsConversationsFilter,
  type CsStatus,
} from '@/lib/tiktokService'
import { useAsync } from '@/lib/useAsync'
import { compareValues } from '@/pages/walmart/walmartSort'
import { CsvButton, nextSort, SortHead, type SortState } from '@/pages/walmart/walmartUi'

import { NO_VALUE, shortDateTime, toMs } from './tiktokFormat'
import { EmptyRows } from './tiktokMoneyUi'
import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  categoryLabel,
  hoursText,
  roleLabel,
  STATUS_LABEL,
  STATUS_TONE,
  statusLabel,
  waitTone,
} from './tiktokServiceFormat'
import { CopyId, ServiceHeader } from './tiktokServiceUi'

const PAGE_SIZE = 25
const MAX_WAIT_FILTER_HOURS = 24 * 90
const STATUSES = Object.keys(STATUS_LABEL) as CsStatus[]

type SortKey = 'id' | 'created' | 'status' | 'category' | 'wait' | 'unread' | 'buyer' | 'seller' | 'bot'
const TEXT_KEYS: readonly SortKey[] = ['id', 'status', 'category']
const INITIAL_SORT: SortState<SortKey> = { key: 'wait', dir: -1 }

function sortValue(c: CsConversation, key: SortKey): string | number | null {
  switch (key) {
    case 'id':
      return c.conversation_id
    case 'created':
      return toMs(c.created_at)
    case 'status':
      return statusLabel(c.status)
    case 'category':
      return categoryLabel(c.category)
    case 'wait':
      return c.wait_hours
    case 'unread':
      return c.unread_count
    case 'buyer':
      return c.buyer_messages
    case 'seller':
      return c.seller_messages
    case 'bot':
      return c.bot_messages
  }
}

/** Metadatos exportables: ningún texto ni dato de comprador existe en estas filas. */
function csvRows(rows: CsConversation[]): CsvRow[] {
  return rows.map((c) => ({
    conversacion: c.conversation_id,
    creada: c.created_at,
    estado: statusLabel(c.status),
    categoria: categoryLabel(c.category),
    espera_horas: c.wait_hours,
    no_leidos: c.unread_count,
    mensajes_comprador: c.buyer_messages,
    mensajes_vendedor: c.seller_messages,
    mensajes_bot: c.bot_messages,
    ultimo_en_escribir: roleLabel(c.last_sender_role),
  }))
}

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T | ''
  options: { value: T; label: string }[]
  onChange: (v: T | '') => void
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground text-xs">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value as T | '')} className="h-9 rounded-md border bg-transparent px-2 text-sm">
        <option value="">Todas</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  const button = 'rounded border px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40'
  return (
    <nav aria-label="Paginación" className="mt-3 flex items-center justify-end gap-3 text-sm">
      <button type="button" className={button} disabled={page === 0} onClick={() => onPage(page - 1)}>
        Anterior
      </button>
      <span className="text-muted-foreground">
        Página {page + 1} de {pages}
      </span>
      <button type="button" className={button} disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>
        Siguiente
      </button>
    </nav>
  )
}

function ConversationRow({ c }: { c: CsConversation }) {
  return (
    <TableRow>
      <TableCell>
        <CopyId id={c.conversation_id} />
      </TableCell>
      <TableCell className="whitespace-nowrap">{shortDateTime(c.created_at)}</TableCell>
      <TableCell>
        <Pill label={statusLabel(c.status)} className={STATUS_TONE[c.status]} />
      </TableCell>
      <TableCell>{categoryLabel(c.category)}</TableCell>
      <TableCell className={`text-right font-medium whitespace-nowrap ${waitTone(c.wait_hours)}`}>{hoursText(c.wait_hours)}</TableCell>
      <TableCell className="text-right">{count(c.unread_count)}</TableCell>
      <TableCell className="text-right">{count(c.buyer_messages)}</TableCell>
      <TableCell className="text-right">{count(c.seller_messages)}</TableCell>
      <TableCell className="text-right">{count(c.bot_messages)}</TableCell>
    </TableRow>
  )
}

/** Lista de conversaciones del servicio al cliente: solo metadatos, sin contenido de mensajes. */
export function TikTokCsConversations() {
  const [status, setStatus] = useState<CsStatus | ''>('')
  const [category, setCategory] = useState<CsCategory | ''>('')
  const [minWait, setMinWait] = useState(0)
  const [humanOnly, setHumanOnly] = useState(false)
  const [sort, setSort] = useState<SortState<SortKey>>(INITIAL_SORT)
  const [page, setPage] = useState(0)

  const filter = useMemo<CsConversationsFilter>(
    () => ({ status: status || undefined, category: category || undefined, minWaitHours: minWait || undefined }),
    [status, category, minWait],
  )
  const data = useAsync(() => tiktokService.conversations(filter), [filter])
  const report = data.data

  /** Cambiar un filtro vuelve a la primera página. */
  const changing =
    <T,>(set: (v: T) => void) =>
    (value: T) => {
      set(value)
      setPage(0)
    }
  const onSort = (key: SortKey) => {
    setSort((cur) => nextSort(cur, key, TEXT_KEYS))
    setPage(0)
  }

  const rows = useMemo(() => {
    const items = (report?.items ?? []).filter((c) => !humanOnly || c.seller_messages > 0)
    return [...items].sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [report, humanOnly, sort])

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const current = Math.min(page, pages - 1)
  const visible = rows.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE)
  const truncated = report != null && report.total > report.items.length
  const loading = data.loading && !report

  return (
    <div className="space-y-6 p-6">
      <ServiceHeader
        title="Conversaciones de servicio al cliente"
        note="Cada fila muestra solo el id (acortado), fechas, estado, categoría, espera y conteos de mensajes por quién escribe. La espera se mide desde el mensaje más viejo sin respuesta humana."
      />

      <div className="flex flex-wrap items-end gap-3">
        <FilterSelect label="Estado" value={status} onChange={changing(setStatus)} options={STATUSES.map((s) => ({ value: s, label: STATUS_LABEL[s] }))} />
        <FilterSelect label="Categoría" value={category} onChange={changing(setCategory)} options={CATEGORY_ORDER.map((c) => ({ value: c, label: CATEGORY_LABEL[c] }))} />
        <NumberField label="Espera mínima (solo las que esperan al vendedor)" value={minWait} min={0} max={MAX_WAIT_FILTER_HOURS} suffix="horas" onCommit={changing(setMinWait)} />
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input type="checkbox" checked={humanOnly} onChange={(e) => changing(setHumanOnly)(e.target.checked)} />
          Solo con intervención humana
        </label>
      </div>

      <ErrorAlert message={data.error} />
      {truncated && (
        <Callout tone="info">
          Hay {count(report.total)} conversaciones con estos filtros; se muestran las {count(MAX_LIST_LIMIT)} con más espera. Afina los filtros para ver el resto.
        </Callout>
      )}

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">Conversaciones ({count(rows.length)})</CardTitle>
          <CsvButton filename="tiktok-servicio-conversaciones.csv" rows={csvRows(rows)} />
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <SortHead sortKey="id" label="Conversación" sort={sort} onSort={onSort} right={false} />
                <SortHead sortKey="created" label="Creada" sort={sort} onSort={onSort} right={false} />
                <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
                <SortHead sortKey="category" label="Categoría" sort={sort} onSort={onSort} right={false} />
                <SortHead sortKey="wait" label="Espera" sort={sort} onSort={onSort} />
                <SortHead sortKey="unread" label="No leídos" sort={sort} onSort={onSort} />
                <SortHead sortKey="buyer" label="Comprador" sort={sort} onSort={onSort} />
                <SortHead sortKey="seller" label="Vendedor" sort={sort} onSort={onSort} />
                <SortHead sortKey="bot" label="Bot" sort={sort} onSort={onSort} />
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((c) => (
                <ConversationRow key={c.conversation_id} c={c} />
              ))}
            </TableBody>
          </Table>
          {visible.length === 0 && <EmptyRows loading={loading} message="Ninguna conversación coincide con los filtros." />}
          {rows.length > PAGE_SIZE && <Pager page={current} pages={pages} onPage={setPage} />}
        </CardContent>
      </Card>
      <p className="text-muted-foreground text-xs">{NO_VALUE} en "Espera" significa que la conversación no está esperando al vendedor.</p>
    </div>
  )
}
