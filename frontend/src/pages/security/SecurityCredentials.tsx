import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Pill, PILL } from '@/components/analytics/ui'
import type { CsvRow } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { security, type CredentialItem, type PlatformCredentials } from '@/lib/security'
import { useAsync } from '@/lib/useAsync'
import { CsvButton, SearchBox } from '@/pages/walmart/walmartUi'

import { dateOnly, expiryBadge, NO_VALUE, yesNo } from './securityFormat'
import { BadgePill, GitPill, LoadingNote, SecurityHeader } from './securityUi'

const COPIES_WARNING = 'Las copias y respaldos contienen secretos viejos: bórralos cuando rotes la credencial.'
const BACKUP_WARNING = 'Los respaldos de la app incluyen archivos de credenciales: protégelos como si fueran las claves mismas.'
const ENV_LOCATION = 'Variable de entorno'

const locationText = (c: CredentialItem) => (c.location === 'env' ? `${ENV_LOCATION} (${c.file})` : c.file)

function matches(c: CredentialItem, label: string, needle: string): boolean {
  if (!needle) return true
  return [c.name, c.kind_label, c.file, label].some((text) => text.toLowerCase().includes(needle))
}

function filterPlatforms(platforms: PlatformCredentials[], search: string): PlatformCredentials[] {
  const needle = search.trim().toLowerCase()
  return platforms
    .map((p) => ({ ...p, credentials: p.credentials.filter((c) => matches(c, p.label, needle)) }))
    .filter((p) => p.credentials.length > 0)
}

function csvRows(platforms: PlatformCredentials[]): CsvRow[] {
  return platforms.flatMap((p) =>
    p.credentials.map((c) => ({
      Plataforma: p.label,
      Nombre: c.name,
      Tipo: c.kind_label,
      Presente: yesNo(c.present),
      Ubicación: locationText(c),
      'Git lo ignora': c.git_ignored == null ? 'Sin verificar' : yesNo(c.git_ignored),
      Vence: c.expires_at ?? '',
      'Días restantes': c.days_left ?? '',
      Copias: c.copies.map((x) => x.file).join(' | '),
      'En el respaldo de la app': yesNo(c.in_app_backup),
    })),
  )
}

function ExpiryCell({ item }: { item: CredentialItem }) {
  if (!item.present || !item.expires_at) return <span className="text-muted-foreground">{NO_VALUE}</span>
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs">{dateOnly(item.expires_at)}</span>
      <BadgePill badge={expiryBadge(item.days_left, item.expired)} />
    </div>
  )
}

function CopiesCell({ item }: { item: CredentialItem }) {
  if (item.copies.length === 0 && !item.in_app_backup) return <span className="text-muted-foreground">{NO_VALUE}</span>
  return (
    <ul className="space-y-1">
      {item.copies.map((c) => (
        <li key={c.file} className="flex flex-wrap items-center gap-1.5">
          <code className="text-xs break-all">{c.file}</code>
          <GitPill ignored={c.git_ignored} />
        </li>
      ))}
      {item.in_app_backup && (
        <li>
          <Pill label="Incluido en el respaldo de la app" className={PILL.red} />
        </li>
      )}
    </ul>
  )
}

function CredentialTable({ items }: { items: CredentialItem[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Nombre</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Presente</TableHead>
          <TableHead>Ubicación</TableHead>
          <TableHead>Git</TableHead>
          <TableHead>Vencimiento</TableHead>
          <TableHead>Copias y respaldos</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((c) => (
          <TableRow key={c.name}>
            <TableCell className="align-top">
              <code className="text-xs break-all">{c.name}</code>
            </TableCell>
            <TableCell className="align-top whitespace-normal">{c.kind_label}</TableCell>
            <TableCell className="align-top">
              <Pill label={c.present ? 'Presente' : 'Ausente'} className={c.present ? PILL.blue : PILL.slate} />
            </TableCell>
            <TableCell className="align-top whitespace-normal">{locationText(c)}</TableCell>
            <TableCell className="align-top">{c.present ? <GitPill ignored={c.git_ignored} /> : <span className="text-muted-foreground">{NO_VALUE}</span>}</TableCell>
            <TableCell className="align-top">
              <ExpiryCell item={c} />
            </TableCell>
            <TableCell className="align-top">
              <CopiesCell item={c} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function PlatformCard({ platform }: { platform: PlatformCredentials }) {
  const hasCopies = platform.credentials.some((c) => c.copies.length > 0)
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">{platform.label}</CardTitle>
        <Pill label={platform.present ? 'Con credenciales' : 'Sin credenciales'} className={platform.present ? PILL.blue : PILL.slate} />
      </CardHeader>
      <CardContent className="space-y-3">
        {hasCopies && <Callout tone="warning">{COPIES_WARNING}</Callout>}
        <CredentialTable items={platform.credentials} />
      </CardContent>
    </Card>
  )
}

/** Inventario de credenciales por plataforma. Solo nombres, estados y fechas: nunca el valor de un secreto. */
export function SecurityCredentials() {
  const [search, setSearch] = useState('')
  const { data, loading, error } = useAsync(() => security.credentials(), [])
  const visible = useMemo(() => (data ? filterPlatforms(data.platforms, search) : []), [data, search])

  return (
    <div className="space-y-4">
      <SecurityHeader
        title="Inventario de credenciales"
        note="Dónde vive cada credencial, si git la ignora y cuándo vence. Aquí nunca se muestran los valores, solo sus nombres."
      >
        <SearchBox value={search} onChange={setSearch} placeholder="Buscar credencial o plataforma" />
        <CsvButton filename="credenciales-metadatos.csv" rows={csvRows(visible)} />
      </SecurityHeader>
      <ErrorAlert message={error} />
      {loading && !data && <LoadingNote />}
      {data?.backup_includes_credential_files && <Callout tone="danger">{BACKUP_WARNING}</Callout>}
      {data && visible.length === 0 && <p className="text-muted-foreground text-sm">Ninguna credencial coincide con la búsqueda.</p>}
      {visible.map((p) => (
        <PlatformCard key={p.platform} platform={p} />
      ))}
    </div>
  )
}
