import { readonly, ref } from 'vue'
import { getRequest } from '@shared/api'
import {
  isCapabilityReadCurrent,
  onCapabilitiesInvalidated,
  onCapabilitiesRefresh,
  parseCapabilities,
  resetCapabilities,
  setSystemCapabilities,
  startCapabilityRead,
  type CapabilityRead,
} from '@shared/permissions/capabilities'

// Who is signed in, as reported by the shared auth API's GET /auth/me. core owns auth
// , so the Topbar only reads this state and never talks to the auth API itself.
// main.ts calls loadCurrentUser() once during bootstrap — that request doubles as the
// token validity check the earlier implementation already performed there — so rendering the Topbar costs no
// extra request.
//
// The same answer carries `capabilities` (what the person may do in the system scope): it goes to the
// shared capability store, which every screen reads instead of working permissions out from `isAdmin`.
//
// `status` tells "the /auth/me answer is still out" apart from "there
// is no administrator". Screens that only an administrator may see (Settings: Project/System)
// wait while it is `loading` and treat every other state without `isAdmin` as not allowed.
//
// v02test.0090 (card 5): the answer also carries the server's own judgment — `capabilities` (the
// system-scope actions this person may do, one entry per permission ID), the teams the person is in
// and the projects they hold a role on. The screens only read these values; they never work out a
// permission from a role name. An older server sends none of the three: the fields stay absent and
// the screens keep the administrator-only behaviour they had.

export interface ActionValue {
  allowed: boolean
  /** Why the action is refused (role_limit, not_member, context_rule, system_admin_only …). */
  reason?: string
  /** The lowest role that holds the action, when the refusal is a role limit. */
  minimum_role?: string
  rule?: string
}

/** One capabilities block: the final role and one value per permission ID. */
export interface CapabilityBlock {
  role?: string | null
  sources?: readonly unknown[]
  actions: Record<string, ActionValue>
}

export interface MyTeam {
  team_id: string
  name: string
  workspace_id: string
}

/** One project the person holds a role on, with where each part of the role came from. */
export interface MyProject {
  project_id: string
  role: string | null
  sources: readonly string[]
  grants: Record<string, string>
}

export interface CurrentUser {
  username: string
  email: string | null
  isAdmin: boolean
  capabilities?: CapabilityBlock
  teams?: readonly MyTeam[]
  projects?: readonly MyProject[]
}

/** idle = never asked, loading = /auth/me in flight, ready = answered (or set), failed = request failed. */
export type CurrentUserStatus = 'idle' | 'loading' | 'ready' | 'failed'

interface MeResponse {
  username?: string | null
  email?: string | null
  is_admin?: boolean
  capabilities?: unknown
  teams?: unknown
  projects?: unknown
}

const current = ref<CurrentUser | null>(null)
const status = ref<CurrentUserStatus>('idle')

function unwrap(data: unknown): MeResponse | null {
  if (!data || typeof data !== 'object') return null
  // Tolerate an enveloped `{ data: {...} }` body the same way the the earlier implementation placeholder did.
  const inner = 'data' in data ? (data as { data: unknown }).data : data
  return inner && typeof inner === 'object' ? (inner as MeResponse) : null
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

function toCapabilities(value: unknown): CapabilityBlock | undefined {
  if (!isRecord(value) || !isRecord(value.actions)) return undefined
  const actions: Record<string, ActionValue> = {}
  for (const [id, raw] of Object.entries(value.actions)) {
    if (!isRecord(raw)) continue
    const entry: ActionValue = { allowed: raw.allowed === true }
    if (typeof raw.reason === 'string') entry.reason = raw.reason
    if (typeof raw.minimum_role === 'string') entry.minimum_role = raw.minimum_role
    if (typeof raw.rule === 'string') entry.rule = raw.rule
    actions[id] = entry
  }
  return { role: typeof value.role === 'string' ? value.role : null, sources: Array.isArray(value.sources) ? value.sources : [], actions }
}

function toTeams(value: unknown): MyTeam[] | undefined {
  if (!Array.isArray(value)) return undefined
  return value.flatMap((item) =>
    isRecord(item) && typeof item.team_id === 'string'
      ? [{ team_id: item.team_id, name: typeof item.name === 'string' ? item.name : item.team_id, workspace_id: typeof item.workspace_id === 'string' ? item.workspace_id : '' }]
      : [],
  )
}

function toProjects(value: unknown): MyProject[] | undefined {
  if (!Array.isArray(value)) return undefined
  return value.flatMap((item) => {
    if (!isRecord(item) || typeof item.project_id !== 'string') return []
    const grants: Record<string, string> = {}
    if (isRecord(item.grants)) {
      for (const [kind, role] of Object.entries(item.grants)) if (typeof role === 'string') grants[kind] = role
    }
    return [{
      project_id: item.project_id,
      role: typeof item.role === 'string' ? item.role : null,
      sources: Array.isArray(item.sources) ? item.sources.filter((s): s is string => typeof s === 'string') : [],
      grants,
    }]
  })
}

export function toCurrentUser(data: unknown): CurrentUser | null {
  const me = unwrap(data)
  if (!me || typeof me.username !== 'string' || me.username === '') return null
  const user: CurrentUser = { username: me.username, email: me.email ?? null, isAdmin: Boolean(me.is_admin) }
  // Only what the server actually sent is set, so an older server's answer reads exactly as before.
  const capabilities = toCapabilities(me.capabilities)
  if (capabilities) user.capabilities = capabilities
  const teams = toTeams(me.teams)
  if (teams) user.teams = teams
  const projects = toProjects(me.projects)
  if (projects) user.projects = projects
  return user
}

/**
 * Publish an /auth/me answer — the person AND the system capability block — if it still belongs to the session
 * that asked and is not older than an answer already published. Every path that reads /auth/me ends here, so a
 * read that left before a sign-out or an account switch cannot fill the new session's user or permissions, and
 * two reads that arrive in the wrong order leave the newer one's state.
 */
function publish(read: CapabilityRead, seq: number, data: unknown, options: { keepWhenUnreadable?: boolean } = {}): CurrentUser | null | undefined {
  if (!isCapabilityReadCurrent(read) || seq !== latestRead) return undefined
  const next = toCurrentUser(data)
  if (!next && options.keepWhenUnreadable) return undefined
  if (!setSystemCapabilities(parseCapabilities(unwrap(data)?.capabilities), read)) return undefined
  // The same answer again (a permission refresh that found nothing new) keeps the object, so screens watching
  // `user` do not re-run for nothing.
  if (next && current.value && JSON.stringify(next) === JSON.stringify(current.value)) {
    status.value = 'ready'
    return current.value
  }
  current.value = next
  status.value = 'ready'
  return next
}

/** The newest /auth/me read that was started; an answer of an older one is dropped. */
let latestRead = 0

/** Fetch /auth/me and remember the result. Rejects when the request fails (invalid token). */
export async function loadCurrentUser(): Promise<CurrentUser | null> {
  status.value = 'loading'
  const read = startCapabilityRead()
  const seq = ++latestRead
  try {
    const response = await getRequest<unknown>('/auth/me')
    const next = publish(read, seq, response.data)
    // Dropped (another session, or a newer read decides): whatever is shown now stays.
    return next === undefined ? current.value : next
  } catch (error) {
    if (isCapabilityReadCurrent(read) && seq === latestRead) {
      current.value = null
      resetCapabilities()
      status.value = 'failed'
    }
    throw error
  }
}

export function setCurrentUser(user: CurrentUser | null): void {
  const switched = !!user && !!current.value && current.value.username !== user.username
  current.value = user
  // Sign-out or another person: nothing the earlier one was told may stay, and answers still on their way are dropped.
  if (!user || switched) resetCapabilities()
  status.value = 'ready'
}

// Permissions changed somewhere (a refusal, a request leaving after a while, coming back to the window, a role
// change made here): ask /auth/me again without going back to `loading`, so no menu flashes away while the answer
// is out. A failed read keeps what is shown; the next refusal, request or return asks again.
async function refreshCurrentUser(): Promise<void> {
  if (status.value !== 'ready' || !current.value) return
  const read = startCapabilityRead()
  const seq = ++latestRead
  try {
    const response = await getRequest<unknown>('/auth/me')
    publish(read, seq, response.data, { keepWhenUnreadable: true })
  } catch {
    // Keep the last answer.
  }
}
onCapabilitiesInvalidated(() => { void refreshCurrentUser() })
onCapabilitiesRefresh(() => { void refreshCurrentUser() })

export function useCurrentUser() {
  return { user: readonly(current), status: readonly(status) }
}
