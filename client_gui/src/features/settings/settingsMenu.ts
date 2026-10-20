import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useCurrentUser } from '@/core/auth/currentUser'
import type { SettingsIconName } from './icons'

// flowgate.v02.0076 T#1 (D §1.1/§1.2): the one list every settings menu is drawn from — the hub
// cards, the scope headings of the settings navigation (components/SettingsNav) and the 시스템
// sub-navigation. A page is offered only when its route is registered in the app router (its view
// exists) and, for 프로젝트/시스템, only once /auth/me has answered with a system administrator. A page whose view is not built yet
// therefore never shows up as a "준비 중" or disabled entry; it appears by registering its route.
// This only narrows what the GUI offers — the server's own permission checks are unchanged.

export type SettingsScopeKey = 'me' | 'project' | 'system'

export interface SettingsPage {
  key: string
  scope: SettingsScopeKey
  /** Route name in the app router; the page is hidden while no route has this name. */
  routeName: string
  /** Path inside the /app router base. */
  path: string
  labelKey: string
  icon: SettingsIconName
  /** Small heading the page is listed under in its scope's sub-navigation (시스템, mockup ⑩). */
  groupKey?: string
}

/** 프로젝트 and 시스템 are shown to system administrators only (D §0.2 노출 결정). */
export const ADMIN_SETTINGS_SCOPES: readonly SettingsScopeKey[] = ['project', 'system']

// D §1.2 order. 프로젝트's 기본 정보 / 저장 경로 / Git are reached from the project selection, not
// from these menus.
export const SETTINGS_PAGES: readonly SettingsPage[] = [
  { key: 'account', scope: 'me', routeName: 'settings-me-account', path: '/settings/me/account', labelKey: 'gui.settings.me_nav.account', icon: 'user' },
  { key: 'security', scope: 'me', routeName: 'settings-me-security', path: '/settings/me/security', labelKey: 'gui.settings.me_nav.security', icon: 'shield' },
  { key: 'project', scope: 'project', routeName: 'settings-project', path: '/settings/project', labelKey: 'gui.settings.project_nav.select', icon: 'folder' },
  { key: 'general', scope: 'system', routeName: 'settings-system-general', path: '/settings/system/general', labelKey: 'gui.settings.system_nav.general', icon: 'gear', groupKey: 'gui.settings.system_groups.ops' },
  { key: 'agents', scope: 'system', routeName: 'settings-system-agents', path: '/settings/system/agents', labelKey: 'gui.settings.system_nav.agents', icon: 'bot', groupKey: 'gui.settings.system_groups.resources' },
  { key: 'users', scope: 'system', routeName: 'settings-system-users', path: '/settings/system/users', labelKey: 'gui.settings.users.title', icon: 'users', groupKey: 'gui.settings.system_groups.accounts' },
  { key: 'projects', scope: 'system', routeName: 'settings-system-projects', path: '/settings/system/projects', labelKey: 'gui.settings.system_nav.projects', icon: 'folder', groupKey: 'gui.settings.system_groups.accounts' },
]

/** The settings pages the signed-in person may open from a menu right now, in menu order. */
export function useSettingsMenu() {
  const router = useRouter()
  const { user, status } = useCurrentUser()
  return computed<SettingsPage[]>(() => {
    const admin = status.value === 'ready' && Boolean(user.value?.isAdmin)
    return SETTINGS_PAGES.filter(
      (page) => router.hasRoute(page.routeName) && (admin || !ADMIN_SETTINGS_SCOPES.includes(page.scope)),
    )
  })
}
