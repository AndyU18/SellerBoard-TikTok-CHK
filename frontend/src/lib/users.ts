// Administración de usuarios (backend: app/users_api.py y app/users_admin_api.py).
import { authFetch, parse } from '@/lib/api'

export type UserStatus = 'active' | 'pending' | 'disabled'

export interface UserRow {
  id: string
  email: string | null
  full_name: string | null
  is_active: boolean
  roles: string[]
  status: UserStatus
}

export interface RoleInfo {
  code: string
  name: string
  permissions: string[]
}

export interface UsersOverview {
  me: { id: string | null; can_manage: boolean; can_manage_owner: boolean }
  active_owners: number
  roles: RoleInfo[]
  users: UserRow[]
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** El backend responde 403 «No tienes permiso para esto (...)»; la pantalla lo distingue de otros errores. */
export const isForbidden = (message: string | null): boolean => message !== null && /No tienes permiso/i.test(message)

export const usersApi = {
  overview: () => authFetch('/api/users/overview').then((r) => parse<UsersOverview>(r)),
  /** Un usuario con un solo rol (o ninguno: queda pendiente). */
  setRole: (userId: string, role: string | null) =>
    authFetch(`/api/users/${userId}/roles`, { method: 'PUT', ...json({ roles: role ? [role] : [] }) }).then((r) =>
      parse<{ id: string; roles: string[] }>(r),
    ),
  setActive: (userId: string, isActive: boolean) =>
    authFetch(`/api/users/${userId}`, { method: 'PATCH', ...json({ is_active: isActive }) }).then((r) =>
      parse<{ id: string; is_active: boolean }>(r),
    ),
}
