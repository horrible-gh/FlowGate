import { readonly, ref } from 'vue'
import type { RouteLocationNormalized, RouteLocationNormalizedLoaded, RouteLocationRaw, Router } from 'vue-router'
import { useCurrentUser, type CurrentUser, type CurrentUserStatus } from '@/core/auth/currentUser'
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
// - Retired screens (/settings/workspaces[/…], /settings/system/security[/…]) → the overview.
// - Screens that need a permission (REQUIREMENTS below: system settings, users, the project screens):
//   wait while /auth/me — or, for the project screens, the project list's capabilities — is still out;
//   anyone the server's capabilities do not allow goes to the overview with a "no permission" notice;
//   a permitted person gets the page if its view is registered, else the overview. Nothing here looks
//   at `is_admin` or a role name: the screen only reads what the server said (flowgate.v02test.0090).
// - any other /settings/* path without a registered view → the overview.
//
// "The overview" is always a replace of /settings without the query/hash of the refused URL,
// so neither the old screen state nor a Back press can bring a refused screen back. Paths
// outside /settings are never touched (their deep-link contract stays as it was). This is a
// GUI exposure rule only; it neither replaces nor changes the server's permission checks.

/** Route name of the settings catch-all: a /settings/* path no view is registered for. */
export const SETTINGS_UNKNOWN_ROUTE = 'settings-unknown'

export type SettingsAccessNotice = 'no_permission'

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
}

/** What the server must allow: any ONE of the system actions, or `project` in at least one visible project. */
export interface SettingsRequirement {
  system: readonly string[]
  project?: string
}

export type SettingsRequirementState = 'allowed' | 'denied' | 'pending'

export interface SettingsAccessAuth {
  status: CurrentUserStatus
  user: Pick<CurrentUser, 'username'> | null
  /** The server's answer for a requirement (`pending` while the answer that decides it is still out). */
  requirement: (requirement: SettingsRequirement) => SettingsRequirementState
}

const SETTINGS_PATH = /^\/settings(\/|$)/i
// Which screens need which permission (first match). A route record marked `meta.settingsAccess: 'admin'`
// that no row names needs `system.settings.manage`. Card "settings screens" adds its own rows here.
const REQUIREMENTS: ReadonlyArray<readonly [RegExp, SettingsRequirement]> = [
  [/^\/settings\/system\/users(\/|$)/i, { system: ['system.user.read'] }],
  [/^\/settings\/system\/(agents|voice)(\/|$)/i, { system: ['system.settings.manage'] }],
  [/^\/settings\/system(\/|$)/i, { system: ['system.settings.read'] }],
  [/^\/settings\/project(\/|$)/i, { system: ['system.settings.read'], project: 'project.settings.read' }],
]
const MANAGE_ONLY: SettingsRequirement = { system: ['system.settings.manage'] }

export function settingsRequirementOf(path: string, markedAdmin = false): SettingsRequirement | null {
  for (const [pattern, requirement] of REQUIREMENTS) if (pattern.test(path)) return requirement
  return markedAdmin ? MANAGE_ONLY : null
}

/** The server's answer for a requirement, from the shared capability store. */
export function settingsRequirementState(requirement: SettingsRequirement): SettingsRequirementState {
  if (requirement.system.some((action) => systemCan(action))) return 'allowed'
  if (requirement.project) {
    const state = projectCapabilityListState()
    if (state === 'idle') {
      void ensureProjectCapabilityList()
      return 'pending'
    }
    if (state === 'loading') return 'pending'
    return anyProjectCan(requirement.project) ? 'allowed' : 'denied'
  }
  return 'denied'
}

/** Menus: only an answer that says yes shows a page (`pending` shows nothing yet). */
export function isSettingsRequirementMet(requirement: SettingsRequirement): boolean {
  return settingsRequirementState(requirement) === 'allowed'
}

/** The system block of /auth/me is in the shared store. */
export function settingsAccessReady(): boolean {
  return systemCapabilitiesReady()
}
const RETIRED_PATHS = [/^\/settings\/workspaces(\/|$)/i, /^\/settings\/system\/security(\/|$)/i]

export function decideSettingsAccess(target: SettingsAccessTarget, auth: SettingsAccessAuth): SettingsAccess {
  if (!SETTINGS_PATH.test(target.path)) return { kind: 'allow' }
  if (RETIRED_PATHS.some((pattern) => pattern.test(target.path))) return { kind: 'home', notice: null }
  const requirement = settingsRequirementOf(target.path, target.adminOnly)
  if (requirement) {
    if (auth.status === 'loading') return { kind: 'pending' }
    if (auth.status !== 'ready' || !auth.user) return { kind: 'home', notice: 'no_permission' }
    const state = auth.requirement(requirement)
    if (state === 'pending') return { kind: 'pending' }
    if (state === 'denied') return { kind: 'home', notice: 'no_permission' }
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
