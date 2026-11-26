import { readonly, ref } from 'vue'
import type { RouteLocationNormalized, RouteLocationNormalizedLoaded, RouteLocationRaw, Router } from 'vue-router'
import { useCurrentUser, type CapabilityBlock, type CurrentUser, type CurrentUserStatus } from '@/core/auth/currentUser'
import { allows, allowsAny, currentAccessLists, reportsCapabilities, type AccessLists } from './capabilities'
import {
  anyProjectCan,
  ensureProjectCapabilityList,
  projectCapabilityListState,
  systemCan,
  systemCapabilitiesReady,
} from '@shared/permissions/capabilities'

// (D): which /app/settings URL may be shown, decided before any
// settings view mounts. The router guard applies it to every move and SettingsLayout applies
// it again when the /auth/me answer arrives, so both follow one rule:
//
// - Retired screen (/settings/system/security[/…]) → the overview.
// - A screen that needs a permission (REQUIREMENTS below: system settings / users, teams, workspace
//   settings, the project screens): wait while /auth/me — or, for a workspace or project screen, the
//   capabilities of the workspaces / projects — is still out; anyone the server's capabilities do not
//   allow goes to the overview with a "no permission" notice; a permitted person gets the page if its
//   view is registered, else the overview. Nothing here works a permission out from `is_admin` or a
//   role name: the screen only reads what the server said (flowgate.v02test.0090). Only a server that
//   sends no capabilities at all (an older one) keeps the rule it had: administrators only, with the
//   "administrators only" notice, and the screens that need the new server API stay closed.
// - any other /settings/* path without a registered view → the overview.
//
// "The overview" is always a replace of /settings without the query/hash of the refused URL,
// so neither the old screen state nor a Back press can bring a refused screen back. Paths
// outside /settings are never touched (their deep-link contract stays as it was). This is a
// GUI exposure rule only; it neither replaces nor changes the server's permission checks.

/** Route name of the settings catch-all: a /settings/* path no view is registered for. */
export const SETTINGS_UNKNOWN_ROUTE = 'settings-unknown'

export type SettingsAccessNotice = 'admin_only' | 'no_permission'

export type SettingsAccess =
  | { kind: 'allow' }
  | { kind: 'pending' }
  | { kind: 'home'; notice: SettingsAccessNotice | null }

export interface SettingsAccessTarget {
  path: string
  /** A view is registered for the path (it is not the settings catch-all). */
  registered: boolean
  /** A matched route record is marked `meta.settingsAccess: 'admin'`. */
  adminOnly: boolean
  /** The action(s) the screen needs; when absent the path decides (see PATH_REQUIREMENTS). */
  requires?: SettingsRequirement | null
}

/**
 * What the server must allow: any ONE of the `system` actions, or any one of the `project` actions in at least one
 * visible project, or any one of the `workspace` actions in at least one visible workspace.
 */
export interface SettingsRequirement {
  system: readonly string[]
  project?: string | readonly string[]
  workspace?: readonly string[]
}

export type SettingsRequirementState = 'allowed' | 'denied' | 'pending'

export interface SettingsAccessAuth {
  status: CurrentUserStatus
  user: Partial<Pick<CurrentUser, 'username' | 'isAdmin' | 'capabilities'>> | null
  /** The server's answer for a requirement (`pending` while the answer that decides it is still out). */
  requirement?: (requirement: SettingsRequirement) => SettingsRequirementState
  /** Without `requirement`: what was read of the workspaces / projects capabilities; absent = not read yet. */
  lists?: AccessLists
}

const SETTINGS_PATH = /^\/settings(\/|$)/i
const RETIRED_PATHS = [/^\/settings\/system\/security(\/|$)/i]
const WORKSPACE_PATH = /^\/settings\/workspaces(\/|$)/i
// Screens that need the new server API (teams, workspace changes, project members): an older server has none.
const MODERN_ONLY_PATHS = [WORKSPACE_PATH, /^\/settings\/manage(\/|$)/i, /^\/settings\/project\/[^/]+\/members(\/|$)/i]

// Which screens need which permission (first match). A route record marked `meta.settingsAccess: 'admin'`
// that no row names needs `system.settings.manage`. Cards that add screens add their own rows here.
const PROJECT_ACTIONS = ['project.settings.read', 'project.member.read'] as const
const REQUIREMENTS: ReadonlyArray<readonly [RegExp, SettingsRequirement]> = [
  [/^\/settings\/system\/users(\/|$)/i, { system: ['system.user.read'] }],
  [/^\/settings\/system\/(agents|voice)(\/|$)/i, { system: ['system.settings.manage'] }],
  [/^\/settings\/system(\/|$)/i, { system: ['system.settings.read'] }],
  [/^\/settings\/manage\/teams(\/|$)/i, { system: [], workspace: ['workspace.team.manage'] }],
  [WORKSPACE_PATH, { system: [], workspace: ['workspace.view'] }],
  [/^\/settings\/project(\/|$)/i, { system: ['system.settings.read'], project: PROJECT_ACTIONS }],
]
const MANAGE_ONLY: SettingsRequirement = { system: ['system.settings.manage'] }

export function settingsRequirementOf(path: string, markedAdmin = false): SettingsRequirement | null {
  for (const [pattern, requirement] of REQUIREMENTS) if (pattern.test(path)) return requirement
  return markedAdmin ? MANAGE_ONLY : null
}

/** The server reports capabilities (a current server): /auth/me carried a system block. An older one sends none. */
export function serverReportsCapabilities(user: Pick<CurrentUser, 'capabilities'> | null | undefined): boolean {
  return reportsCapabilities(user) || systemCapabilitiesReady()
}

const asList = (value: string | readonly string[] | undefined): readonly string[] => (typeof value === 'string' ? [value] : value ?? [])

/** Where the answers for the workspace / project part of a requirement are read from. */
interface RequirementSources {
  project: (actions: readonly string[]) => SettingsRequirementState
  workspace: (actions: readonly string[]) => SettingsRequirementState
}

/** Sources that read a lists snapshot (the rows of GET /workspaces and GET /projects, each with its capabilities). */
function listSources(lists: AccessLists | undefined): RequirementSources {
  const from = (pick: (all: AccessLists) => ReadonlyArray<{ capabilities?: CapabilityBlock }>) =>
    (actions: readonly string[]): SettingsRequirementState => {
      const state = lists?.status ?? 'idle'
      if (state === 'idle' || state === 'loading') return 'pending'
      if (state === 'failed' || !lists) return 'denied'
      return pick(lists).some((row) => allowsAny(row.capabilities, actions)) ? 'allowed' : 'denied'
    }
  return { project: from((all) => all.projects), workspace: from((all) => all.workspaces) }
}

/** The projects the person can see, from the shared capability store (read once, asked again after an invalidation). */
function projectState(actions: readonly string[]): SettingsRequirementState {
  const state = projectCapabilityListState()
  if (state === 'idle') {
    void ensureProjectCapabilityList()
    return 'pending'
  }
  if (state === 'loading') return 'pending'
  return actions.some((action) => anyProjectCan(action)) ? 'allowed' : 'denied'
}

/** Any one 'allowed' opens the screen; otherwise it waits while any answer is still out. */
function anyOf(states: readonly SettingsRequirementState[]): SettingsRequirementState {
  if (states.includes('allowed')) return 'allowed'
  return states.includes('pending') ? 'pending' : 'denied'
}

function evaluateSettingsRequirement(
  requirement: SettingsRequirement,
  user: SettingsAccessAuth['user'],
  sources: RequirementSources,
): SettingsRequirementState {
  // An older server sends no capabilities at all: administrators only, exactly the previous rule.
  if (!serverReportsCapabilities(user)) return user?.isAdmin ? 'allowed' : 'denied'
  // The system block of /auth/me, as the shared store and as the person's own `capabilities` (the same answer).
  if (requirement.system.some((action) => systemCan(action) || allows(user?.capabilities, action))) return 'allowed'
  const states: SettingsRequirementState[] = []
  if (requirement.project) states.push(sources.project(asList(requirement.project)))
  if (requirement.workspace?.length) states.push(sources.workspace(requirement.workspace))
  return anyOf(states)
}

/** The server's answer for a requirement, from the shared capability store (system, projects) and the workspace lists. */
export function settingsRequirementState(requirement: SettingsRequirement): SettingsRequirementState {
  const { user } = useCurrentUser()
  return evaluateSettingsRequirement(requirement, user.value, {
    project: projectState,
    workspace: listSources(currentAccessLists()).workspace,
  })
}

/** Menus: only an answer that says yes shows a page (`pending` shows nothing yet). */
export function isSettingsRequirementMet(requirement: SettingsRequirement): boolean {
  return settingsRequirementState(requirement) === 'allowed'
}

/** The notice for a refusal: an older server knows only administrators and says so; otherwise "no permission". */
function refusalNotice(user: SettingsAccessAuth['user']): SettingsAccessNotice {
  return user && !serverReportsCapabilities(user) ? 'admin_only' : 'no_permission'
}

export function decideSettingsAccess(target: SettingsAccessTarget, auth: SettingsAccessAuth): SettingsAccess {
  if (!SETTINGS_PATH.test(target.path)) return { kind: 'allow' }
  if (RETIRED_PATHS.some((pattern) => pattern.test(target.path))) return { kind: 'home', notice: null }
  // An older server has no teams / workspace / member API for these screens: they stay closed.
  if (MODERN_ONLY_PATHS.some((pattern) => pattern.test(target.path)) && auth.status === 'ready' && !serverReportsCapabilities(auth.user)) {
    return { kind: 'home', notice: null }
  }
  const requirement = target.requires ?? settingsRequirementOf(target.path, target.adminOnly)
  if (requirement) {
    if (auth.status === 'loading') return { kind: 'pending' }
    if (auth.status !== 'ready' || !auth.user) return { kind: 'home', notice: refusalNotice(auth.user) }
    const { user } = auth
    const state = auth.requirement ? auth.requirement(requirement) : evaluateSettingsRequirement(requirement, user, listSources(auth.lists))
    if (state === 'pending') return { kind: 'pending' }
    if (state === 'denied') return { kind: 'home', notice: refusalNotice(user) }
  }
  return target.registered ? { kind: 'allow' } : { kind: 'home', notice: null }
}

export function settingsAccessTarget(to: RouteLocationNormalized | RouteLocationNormalizedLoaded): SettingsAccessTarget {
  return {
    path: to.path,
    registered: to.name !== SETTINGS_UNKNOWN_ROUTE,
    adminOnly: to.matched.some((record) => record.meta.settingsAccess === 'admin'),
  }
}

/** The decision for `to` with the signed-in person as known right now. */
export function currentSettingsAccess(to: RouteLocationNormalized | RouteLocationNormalizedLoaded): SettingsAccess {
  const { user, status } = useCurrentUser()
  return decideSettingsAccess(settingsAccessTarget(to), {
    status: status.value,
    user: user.value,
    requirement: settingsRequirementState,
  })
}

// The overview shows the notice of the last refused move once (SettingsHubView clears it).
const notice = ref<SettingsAccessNotice | null>(null)

export function useSettingsAccessNotice() {
  return {
    notice: readonly(notice),
    clearNotice: () => {
      notice.value = null
    },
  }
}

/** Record why a move was refused and return the overview location to replace it with. */
export function settingsHome(access: { notice: SettingsAccessNotice | null }): RouteLocationRaw {
  notice.value = access.notice
  return { path: '/settings', replace: true }
}

export function installSettingsAccessGuard(router: Router): void {
  router.beforeEach((to) => {
    const access = currentSettingsAccess(to)
    // `pending` lets the move finish: SettingsLayout keeps the view unmounted until /auth/me answers.
    return access.kind === 'home' ? settingsHome(access) : true
  })
}
