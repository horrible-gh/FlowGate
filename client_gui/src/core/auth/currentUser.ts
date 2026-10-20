import { readonly, ref } from 'vue'
import { getRequest } from '@shared/api'

// Who is signed in, as reported by the shared auth API's GET /auth/me. core owns auth
// (T0011 §6), so the Topbar only reads this state and never talks to the auth API itself.
// main.ts calls loadCurrentUser() once during bootstrap — that request doubles as the
// token validity check T#1 already performed there — so rendering the Topbar costs no
// extra request.
//
// flowgate.v02.0076 T#1: `status` tells "the /auth/me answer is still out" apart from "there
// is no administrator". Screens that only an administrator may see (환경 설정 프로젝트/시스템)
// wait while it is `loading` and treat every other state without `isAdmin` as not allowed.

export interface CurrentUser {
  username: string
  email: string | null
  isAdmin: boolean
}

/** idle = never asked, loading = /auth/me in flight, ready = answered (or set), failed = request failed. */
export type CurrentUserStatus = 'idle' | 'loading' | 'ready' | 'failed'

interface MeResponse {
  username?: string | null
  email?: string | null
  is_admin?: boolean
}

const current = ref<CurrentUser | null>(null)
const status = ref<CurrentUserStatus>('idle')

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
  status.value = 'loading'
  try {
    const response = await getRequest<unknown>('/auth/me')
    current.value = toCurrentUser(response.data)
    status.value = 'ready'
    return current.value
  } catch (error) {
    current.value = null
    status.value = 'failed'
    throw error
  }
}

export function setCurrentUser(user: CurrentUser | null): void {
  current.value = user
  status.value = 'ready'
}

export function useCurrentUser() {
  return { user: readonly(current), status: readonly(status) }
}
