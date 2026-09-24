import { readonly, ref } from 'vue'
import { getRequest } from '@shared/api'

// Who is signed in, as reported by the shared auth API's GET /auth/me. core owns auth
// (T0011 §6), so the Topbar only reads this state and never talks to the auth API itself.
// main.ts calls loadCurrentUser() once during bootstrap — that request doubles as the
// token validity check T#1 already performed there — so rendering the Topbar costs no
// extra request.

export interface CurrentUser {
  username: string
  email: string | null
  isAdmin: boolean
}

interface MeResponse {
  username?: string | null
  email?: string | null
  is_admin?: boolean
}

const current = ref<CurrentUser | null>(null)

function unwrap(data: unknown): MeResponse | null {
  if (!data || typeof data !== 'object') return null
  // Tolerate an enveloped `{ data: {...} }` body the same way the T#1 placeholder did.
  const inner = 'data' in data ? (data as { data: unknown }).data : data
  return inner && typeof inner === 'object' ? (inner as MeResponse) : null
}

export function toCurrentUser(data: unknown): CurrentUser | null {
  const me = unwrap(data)
  if (!me || typeof me.username !== 'string' || me.username === '') return null
  return { username: me.username, email: me.email ?? null, isAdmin: Boolean(me.is_admin) }
}

/** Fetch /auth/me and remember the result. Rejects when the request fails (invalid token). */
export async function loadCurrentUser(): Promise<CurrentUser | null> {
  const response = await getRequest<unknown>('/auth/me')
  current.value = toCurrentUser(response.data)
  return current.value
}

export function setCurrentUser(user: CurrentUser | null): void {
  current.value = user
}

export function useCurrentUser() {
  return { user: readonly(current) }
}
