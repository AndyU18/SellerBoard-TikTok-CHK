import { useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { isForbidden, usersApi, type RoleInfo, type UserRow, type UserStatus } from '@/lib/users'
import { useAsync } from '@/lib/useAsync'

const SELECT = 'h-9 rounded-md border bg-white px-2 text-sm disabled:opacity-60 dark:bg-slate-900'
const OWNER = 'owner'

const STATUS: Record<UserStatus, { label: string; pill: string; explain: string }> = {
  active: { label: 'Activo', pill: PILL.green, explain: 'Tiene acceso según su rol.' },
  pending: { label: 'Pendiente', pill: PILL.amber, explain: 'Se registró pero aún no tiene rol: no puede ver datos.' },
  disabled: { label: 'Desactivado', pill: PILL.red, explain: 'Sin acceso aunque tenga rol.' },
}

/** Cambio que espera confirmación del administrador. */
type Change =
  | { kind: 'role'; user: UserRow; role: string | null }
  | { kind: 'active'; user: UserRow; isActive: boolean }

const who = (u: UserRow) => u.full_name || u.email || u.id

function describe(change: Change, roles: RoleInfo[]): string {
  if (change.kind === 'active')
    return change.isActive
      ? `¿Reactivar a ${who(change.user)}? Recuperará el acceso de su rol.`
      : `¿Desactivar a ${who(change.user)}? Perderá el acceso de inmediato.`
  const name = roles.find((r) => r.code === change.role)?.name
  return change.role
    ? `¿Dar el rol «${name ?? change.role}» a ${who(change.user)}? Reemplaza su rol actual.`
    : `¿Quitar el rol a ${who(change.user)}? Quedará pendiente y sin acceso a los datos.`
}

export function Users() {
  const [version, setVersion] = useState(0)
  const data = useAsync(() => usersApi.overview(), [version])
  const [change, setChange] = useState<Change | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const d = data.data
  const forbidden = isForbidden(data.error)
  const canManage = d?.me.can_manage ?? false

  async function confirm() {
    if (!change) return
    setSaving(true)
    setActionError(null)
    setSaved(null)
    try {
      if (change.kind === 'role') await usersApi.setRole(change.user.id, change.role)
      else await usersApi.setActive(change.user.id, change.isActive)
      setSaved(`Cambio guardado para ${who(change.user)}.`)
      setChange(null)
      setVersion((v) => v + 1)
    } catch (e) {
      // Los mensajes del backend ya son claros (p. ej. «Debe quedar al menos un Dueño activo»).
      setActionError(e instanceof Error ? e.message : 'No se pudo guardar el cambio')
      setChange(null)
    } finally {
      setSaving(false)
    }
  }

  /** ¿Puede el administrador cambiar el rol de este usuario desde aquí? */
  function lockReason(u: UserRow): string | null {
    if (!d) return null
    if (!canManage) return 'No tienes permiso para administrar usuarios'
    if (u.roles.includes(OWNER) && !d.me.can_manage_owner) return 'Solo un Dueño puede cambiar a otro Dueño'
    if (u.id === d.me.id && u.roles.includes(OWNER)) return 'No puedes quitarte tu propio acceso de Dueño'
    return null
  }

  const counts = {
    active: d?.users.filter((u) => u.status === 'active').length ?? 0,
    pending: d?.users.filter((u) => u.status === 'pending').length ?? 0,
    disabled: d?.users.filter((u) => u.status === 'disabled').length ?? 0,
  }

  if (forbidden)
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold">Usuarios</h1>
        <Callout tone="warning">
          No tienes permiso para ver los usuarios. Pídele al Dueño que te dé el acceso de administración.
        </Callout>
      </div>
    )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Usuarios</h1>
        <Hint>
          Quien se registra queda «Pendiente» hasta que le des un rol. Siempre debe quedar al menos un Dueño activo.
        </Hint>
      </div>
      <ErrorAlert message={data.error} />
      {actionError && <Callout tone="danger">{actionError}</Callout>}
      {saved && <Callout tone="info">{saved}</Callout>}
      {d && !canManage && <Callout tone="info">Puedes ver los usuarios pero no modificarlos.</Callout>}

      {change && (
        <div role="alertdialog" aria-label="Confirmar cambio" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="mb-2 font-medium">{d && describe(change, d.roles)}</p>
          <div className="flex gap-2">
            <Button type="button" disabled={saving} onClick={confirm}>
              {saving ? 'Guardando…' : 'Confirmar'}
            </Button>
            <Button type="button" variant="outline" disabled={saving} onClick={() => setChange(null)}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-4">
        <Kpi title="Activos" value={String(counts.active)} />
        <Kpi
          title="Pendientes"
          value={String(counts.pending)}
          hint="esperan un rol"
          className={counts.pending > 0 ? 'text-amber-600' : ''}
        />
        <Kpi title="Desactivados" value={String(counts.disabled)} />
        <Kpi title="Dueños activos" value={String(d?.active_owners ?? 0)} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Usuarios y roles</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Usuario</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Rol</TableHead>
                <TableHead className="text-right">Acceso</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(d?.users ?? []).map((u) => {
                const lock = lockReason(u)
                const current = u.roles[0] ?? ''
                const st = STATUS[u.status]
                return (
                  <TableRow key={u.id}>
                    <TableCell>
                      <div className="font-medium">{u.full_name || u.email || u.id}</div>
                      {u.full_name && u.email && <div className="text-muted-foreground text-xs">{u.email}</div>}
                      {u.id === d?.me.id && <span className="text-muted-foreground text-xs">(tú)</span>}
                    </TableCell>
                    <TableCell title={st.explain}>
                      <Pill label={st.label} className={st.pill} />
                    </TableCell>
                    <TableCell>
                      <select
                        className={SELECT}
                        aria-label={`Rol de ${who(u)}`}
                        value={current}
                        disabled={lock !== null || saving}
                        title={lock ?? undefined}
                        onChange={(e) => setChange({ kind: 'role', user: u, role: e.target.value || null })}
                      >
                        <option value="">Sin rol (pendiente)</option>
                        {(d?.roles ?? []).map((r) => (
                          <option key={r.code} value={r.code} disabled={r.code === OWNER && !d?.me.can_manage_owner}>
                            {r.name}
                          </option>
                        ))}
                      </select>
                      {u.roles.length > 1 && (
                        <div className="text-muted-foreground mt-1 text-xs">Tiene varios roles: {u.roles.join(', ')}</div>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={!canManage || saving || u.id === d?.me.id}
                        title={u.id === d?.me.id ? 'No puedes desactivarte a ti mismo' : undefined}
                        onClick={() => setChange({ kind: 'active', user: u, isActive: !u.is_active })}
                      >
                        {u.is_active ? 'Desactivar' : 'Reactivar'}
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
              {d && d.users.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} className="text-muted-foreground text-center">
                    Aún no hay usuarios.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Qué puede hacer cada rol</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          {(d?.roles ?? []).map((r) => (
            <div key={r.code} className="rounded-md border p-3">
              <div className="font-medium">{r.name}</div>
              <div className="text-muted-foreground mt-1 text-xs">{r.permissions.join(', ') || 'Sin permisos'}</div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}
