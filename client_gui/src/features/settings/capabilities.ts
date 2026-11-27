import { readonly, ref, watch } from 'vue'
import { useCurrentUser, type CapabilityBlock, type CurrentUser, type ActionValue } from '@/core/auth/currentUser'
import { listAllProjects, listWorkspaces } from './api'

// v02test.0090 card 5 — what the settings screens may offer, read from the server's own answers.
//
// The server sends capabilities for every target: GET /auth/me for the system scope, one block per
// workspace in GET /workspaces and one per project in GET /projects. A block holds one entry per
// permission ID (`allowed`, or refused with a `reason`). The screens only look entries up here; no
// role name is turned into a permission anywhere on the GUI.
//
// An older server sends no capabilities. Then the screens keep the rule they had before: only a
// system administrator (`isAdmin`) opens the project / system screens, and the new screens (teams,
// workspace settings, project members) are not offered at all.

export type RequirementScope = 'system' | 'workspace' | 'project'

/** A screen opens when ANY of `actions` is allowed on at least one target of `scope`. */
export interface Requirement {
  scope: RequirementScope
  actions: readonly string[]
}

export type Verdict = 'allow' | 'deny' | 'pending'
export type ListsStatus = 'idle' | 'loading' | 'ready' | 'failed'

export interface WorkspaceCapRow {
  workspace_id: string
  name: string
  status: string
  capabilities?: CapabilityBlock
}

export interface ProjectCapRow {
  project_id: string
  project_name: string
  is_active: boolean
  capabilities?: CapabilityBlock
}

export interface AccessLists {
  status: ListsStatus
  workspaces: readonly WorkspaceCapRow[]
  projects: readonly ProjectCapRow[]
}

const SYSTEM_PROJECT_ID = '__SYSTEM__'

const status = ref<ListsStatus>('idle')
const workspaces = ref<WorkspaceCapRow[]>([])
const projects = ref<ProjectCapRow[]>([])
let inflight: Promise<void> | null = null

/** The entry the server sent for one permission ID, or undefined when the block has none. */
export function actionOf(block: Pick<CapabilityBlock, 'actions'> | null | undefined, action: string): ActionValue | undefined {
  return block?.actions?.[action]
}

/** True when the server marked `action` allowed in this block. A missing block or entry is refused. */
export function allows(block: Pick<CapabilityBlock, 'actions'> | null | undefined, action: string): boolean {
  return actionOf(block, action)?.allowed === true
}

export function allowsAny(block: Pick<CapabilityBlock, 'actions'> | null | undefined, actions: readonly string[]): boolean {
  return actions.some((action) => allows(block, action))
}

/** The server reports capabilities (a current server). An older one does not. */
export const reportsCapabilities = (user: Pick<CurrentUser, 'capabilities'> | null | undefined): boolean => Boolean(user?.capabilities)

/** The server's reason a refused action is off (for the one-line explanation), or null when allowed / unknown. */
export function refusalReason(block: Pick<CapabilityBlock, 'actions'> | null | undefined, action: string): string | null {
  const value = actionOf(block, action)
  return value && !value.allowed ? value.reason ?? 'forbidden' : null
}

export function evaluateRequirement(
  requirement: Requirement,
  user: Pick<CurrentUser, 'isAdmin' | 'capabilities'> | null | undefined,
  lists: AccessLists | null | undefined,
): Verdict {
  if (!user) return 'deny'
  // An older server: administrators only, exactly the previous rule.
  if (!user.capabilities) return user.isAdmin ? 'allow' : 'deny'
  if (requirement.scope === 'system') return allowsAny(user.capabilities, requirement.actions) ? 'allow' : 'deny'
  const state = lists?.status ?? 'idle'
  if (state === 'idle' || state === 'loading') return 'pending'
  if (state === 'failed' || !lists) return 'deny'
  const rows = requirement.scope === 'workspace' ? lists.workspaces : lists.projects
  return rows.some((row) => allowsAny(row.capabilities, requirement.actions)) ? 'allow' : 'deny'
}

function toCapabilities(value: unknown): CapabilityBlock | undefined {
  if (!value || typeof value !== 'object') return undefined
  const actions = (value as { actions?: unknown }).actions
  if (!actions || typeof actions !== 'object') return undefined
  return value as CapabilityBlock
}

function toProjectRows(value: unknown): ProjectCapRow[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    const row = item as Record<string, unknown> | null
    if (!row || typeof row.project_id !== 'string' || row.project_id === SYSTEM_PROJECT_ID) return []
    return [{
      project_id: row.project_id,
      project_name: typeof row.project_name === 'string' ? row.project_name : row.project_id,
      is_active: row.is_active === true || row.is_active === 1,
      capabilities: toCapabilities(row.capabilities),
    }]
  })
}

/**
 * Read the workspace list and the project list once (the two GETs whose rows carry capabilities).
 * A failed read is `failed` and denies the screens that need it; `force` reads again.
 */
export function loadSettingsAccess(force = false): Promise<void> {
  if (!force && status.value === 'ready') return Promise.resolve()
  if (inflight) return inflight
  status.value = 'loading'
  inflight = (async () => {
    try {
      const [workspaceAnswer, projectAnswer] = await Promise.all([listWorkspaces(), listAllProjects()])
      workspaces.value = (workspaceAnswer?.workspaces ?? []).map((row) => ({
        workspace_id: row.workspace_id,
        name: row.name,
        status: row.status,
        capabilities: toCapabilities((row as { capabilities?: unknown }).capabilities),
      }))
      projects.value = toProjectRows(projectAnswer)
      status.value = 'ready'
    } catch {
      workspaces.value = []
      projects.value = []
      status.value = 'failed'
    } finally {
      inflight = null
    }
  })()
  return inflight
}

/** Forget what was read (another person signed in, or the roles changed): the next screen reads again. */
export function resetSettingsAccess(): void {
  status.value = 'idle'
  workspaces.value = []
  projects.value = []
  inflight = null
}

/** The lists as the router guard and the menus read them. */
export function currentAccessLists(): AccessLists {
  return { status: status.value, workspaces: workspaces.value, projects: projects.value }
}

export function useAccessLists() {
  return { status: readonly(status), workspaces: readonly(workspaces), projects: readonly(projects), reload: () => loadSettingsAccess(true) }
}

// Another person's capabilities are not the previous person's.
const { user } = useCurrentUser()
watch(() => user.value?.username ?? '', () => resetSettingsAccess())
