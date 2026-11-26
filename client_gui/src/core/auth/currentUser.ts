import { readonly, ref } from 'vue'
import { getRequest } from '@shared/api'

// Who is signed in, as reported by the shared auth API's GET /auth/me. core owns auth
// , so the Topbar only reads this state and never talks to the auth API itself.
// main.ts calls loadCurrentUser() once during bootstrap — that request doubles as the
// token validity check the earlier implementation already performed there — so rendering the Topbar costs no
// extra request.
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
