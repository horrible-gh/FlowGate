import { readonly, ref } from 'vue'
import type { RouteLocationNormalized, RouteLocationNormalizedLoaded, RouteLocationRaw, Router } from 'vue-router'
import { useCurrentUser, type CurrentUser, type CurrentUserStatus } from '@/core/auth/currentUser'

// flowgate.v02.0076 T#1 (D §1.2/§3.2): which /app/settings URL may be shown, decided before any
// settings view mounts. The router guard applies it to every move and SettingsLayout applies
// it again when the /auth/me answer arrives, so both follow one rule:
//
// - 폐지된 화면 (/settings/workspaces[/…], /settings/system/security[/…]) → the overview.
// - 프로젝트 / 시스템 (/settings/project[/…], /settings/system[/…]): wait while /auth/me is still
//   out; anyone who is not a system administrator goes to the overview with an "관리자 전용"
//   notice; an administrator gets the page if its view is registered, else the overview.
// - any other /settings/* path without a registered view → the overview.
//
// "The overview" is always a replace of /settings without the query/hash of the refused URL,
// so neither the old screen state nor a Back press can bring a refused screen back. Paths
// outside /settings are never touched (their deep-link contract stays as it was). This is a
// GUI exposure rule only; it neither replaces nor changes the server's permission checks.

/** Route name of the settings catch-all: a /settings/* path no view is registered for. */
export const SETTINGS_UNKNOWN_ROUTE = 'settings-unknown'

export type SettingsAccessNotice = 'admin_only'

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

export interface SettingsAccessAuth {
  status: CurrentUserStatus
  user: Pick<CurrentUser, 'isAdmin'> | null
}

const SETTINGS_PATH = /^\/settings(\/|$)/i
const ADMIN_PATH = /^\/settings\/(system|project)(\/|$)/i
const RETIRED_PATHS = [/^\/settings\/workspaces(\/|$)/i, /^\/settings\/system\/security(\/|$)/i]

export function decideSettingsAccess(target: SettingsAccessTarget, auth: SettingsAccessAuth): SettingsAccess {
  if (!SETTINGS_PATH.test(target.path)) return { kind: 'allow' }
  if (RETIRED_PATHS.some((pattern) => pattern.test(target.path))) return { kind: 'home', notice: null }
  if (target.adminOnly || ADMIN_PATH.test(target.path)) {
    if (auth.status === 'loading') return { kind: 'pending' }
    if (auth.status !== 'ready' || !auth.user?.isAdmin) return { kind: 'home', notice: 'admin_only' }
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
  return decideSettingsAccess(settingsAccessTarget(to), { status: status.value, user: user.value })
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
