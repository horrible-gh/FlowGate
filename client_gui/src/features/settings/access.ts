import { readonly, ref } from 'vue'
import type { RouteLocationNormalized, RouteLocationNormalizedLoaded, RouteLocationRaw, Router } from 'vue-router'
import { useCurrentUser, type CurrentUser, type CurrentUserStatus } from '@/core/auth/currentUser'
import { currentAccessLists, evaluateRequirement, type AccessLists, type Requirement } from './capabilities'

// (D): which /app/settings URL may be shown, decided before any
// settings view mounts. The router guard applies it to every move and SettingsLayout applies
// it again when the /auth/me answer arrives, so both follow one rule:
//
// - Retired screen (/settings/system/security[/…]) → the overview.
// - A screen that needs a server action (system settings / users, workspace settings, teams,
//   project settings / members): wait while /auth/me is still out (and, for a workspace or project
//   screen, while the capabilities of the workspaces / projects are being read); anyone the server
//   does not allow goes to the overview with a notice; someone allowed gets the page if its view is
//   registered, else the overview. v02test.0090: the allowance is the server's capabilities — before,
//   it was "system administrator or not". A server that sends no capabilities (older) keeps the old
//   rule: administrators only, and the Workspace screens stay retired.
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
  requires?: Requirement | null
}

export interface SettingsAccessAuth {
  status: CurrentUserStatus
  user: Pick<CurrentUser, 'isAdmin' | 'capabilities'> | null
  /** What was read of the workspaces / projects capabilities; absent = not read yet. */
  lists?: AccessLists
}

const SETTINGS_PATH = /^\/settings(\/|$)/i
const RETIRED_PATHS = [/^\/settings\/system\/security(\/|$)/i]
const WORKSPACE_PATH = /^\/settings\/workspaces(\/|$)/i
// Screens that need the new server API (teams, workspace changes, project members): an older server has none.
const MODERN_ONLY_PATHS = [WORKSPACE_PATH, /^\/settings\/manage(\/|$)/i, /^\/settings\/project\/[^/]+\/members(\/|$)/i]
const SYSTEM_SETTINGS_READ: Requirement = { scope: 'system', actions: ['system.settings.read'] }

// First match wins. Case-insensitive like the router itself.
const PATH_REQUIREMENTS: { pattern: RegExp; requires: Requirement }[] = [
  { pattern: /^\/settings\/system\/users(\/|$)/i, requires: { scope: 'system', actions: ['system.user.read'] } },
  { pattern: /^\/settings\/system(\/|$)/i, requires: SYSTEM_SETTINGS_READ },
  { pattern: /^\/settings\/manage\/teams(\/|$)/i, requires: { scope: 'workspace', actions: ['workspace.team.manage'] } },
  { pattern: WORKSPACE_PATH, requires: { scope: 'workspace', actions: ['workspace.view'] } },
  { pattern: /^\/settings\/project(\/|$)/i, requires: { scope: 'project', actions: ['project.settings.read', 'project.member.read'] } },
]

/** The notice for a refusal: system screens are "administrators only", the rest "no permission". */
function refusalNotice(requires: Requirement, user: SettingsAccessAuth['user']): SettingsAccessNotice {
  return user?.capabilities && requires.scope !== 'system' ? 'no_permission' : 'admin_only'
}

export function decideSettingsAccess(target: SettingsAccessTarget, auth: SettingsAccessAuth): SettingsAccess {
  if (!SETTINGS_PATH.test(target.path)) return { kind: 'allow' }
  if (RETIRED_PATHS.some((pattern) => pattern.test(target.path))) return { kind: 'home', notice: null }
  // An older server has no teams / workspace / member API for these screens: they stay closed.
  if (MODERN_ONLY_PATHS.some((pattern) => pattern.test(target.path)) && auth.status === 'ready' && !auth.user?.capabilities) {
    return { kind: 'home', notice: null }
  }
  const requires =
    target.requires ?? PATH_REQUIREMENTS.find((entry) => entry.pattern.test(target.path))?.requires ?? (target.adminOnly ? SYSTEM_SETTINGS_READ : null)
  if (requires) {
    if (auth.status === 'loading') return { kind: 'pending' }
    if (auth.status !== 'ready' || !auth.user) return { kind: 'home', notice: 'admin_only' }
    const verdict = evaluateRequirement(requires, auth.user, auth.lists)
    if (verdict === 'pending') return { kind: 'pending' }
    if (verdict === 'deny') return { kind: 'home', notice: refusalNotice(requires, auth.user) }
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
  return decideSettingsAccess(settingsAccessTarget(to), { status: status.value, user: user.value, lists: currentAccessLists() })
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
