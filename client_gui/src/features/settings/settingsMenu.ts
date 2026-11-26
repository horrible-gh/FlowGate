import { computed, watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { useCurrentUser } from '@/core/auth/currentUser'
import { allows, evaluateRequirement, loadSettingsAccess, reportsCapabilities, useAccessLists, type Requirement } from './capabilities'
import type { SettingsIconName } from './icons'

// (D): the one list every settings menu is drawn from — the hub
// cards, the scope headings of the settings navigation (components/SettingsNav) and the sub-navigations.
// A page is offered only when its route is registered in the app router (its view exists) and the
// server's own capabilities allow it (./capabilities): 사용자 and 시스템 follow the system-scope actions of
// /auth/me, 팀 · 워크스페이스 설정 · 프로젝트 follow the capabilities of the workspaces and projects the
// person can see. A page whose view is not built yet never shows up as a "준비 중" or disabled entry; it
// appears by registering its route. v02test.0090: before, 프로젝트 / 시스템 were for system administrators
// only; an older server that sends no capabilities still gets exactly that.
// This only narrows what the GUI offers — the server's own permission checks are unchanged.

export type SettingsScopeKey = 'me' | 'manage' | 'project' | 'system'

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
  /** The server action(s) that open the page; a page without one is open to everyone signed in. */
  requires?: Requirement
  /** Offered only when the server reports capabilities (the screens that need the new server API). */
  modern?: boolean
}

const SYSTEM_READ: Requirement = { scope: 'system', actions: ['system.settings.read'] }

// D §1.2 order. 프로젝트's 기본 정보 / 저장 경로 / Git / 멤버 are reached from the project selection, not
// from these menus.
export const SETTINGS_PAGES: readonly SettingsPage[] = [
  { key: 'account', scope: 'me', routeName: 'settings-me-account', path: '/settings/me/account', labelKey: 'gui.settings.me_nav.account', icon: 'user' },
  { key: 'theme', scope: 'me', routeName: 'settings-me-theme', path: '/settings/me/theme', labelKey: 'gui.taskbar.theme', icon: 'gear' },
  { key: 'agents', scope: 'me', routeName: 'settings-me-agents', path: '/settings/me/agents', labelKey: 'gui.agent_screen.title', icon: 'bot' },
  { key: 'security', scope: 'me', routeName: 'settings-me-security', path: '/settings/me/security', labelKey: 'gui.settings.me_nav.security', icon: 'shield' },
  { key: 'teams', scope: 'manage', modern: true, requires: { scope: 'workspace', actions: ['workspace.team.manage'] }, routeName: 'settings-manage-teams', path: '/settings/manage/teams', labelKey: 'gui.settings.manage_nav.teams', icon: 'users' },
  { key: 'workspace', scope: 'manage', modern: true, requires: { scope: 'workspace', actions: ['workspace.view'] }, routeName: 'settings-workspaces', path: '/settings/workspaces', labelKey: 'gui.settings.manage_nav.workspaces', icon: 'grid' },
  { key: 'project', scope: 'project', requires: { scope: 'project', actions: ['project.settings.read', 'project.member.read'] }, routeName: 'settings-project', path: '/settings/project', labelKey: 'gui.settings.project_nav.select', icon: 'folder' },
  { key: 'general', scope: 'system', requires: SYSTEM_READ, routeName: 'settings-system-general', path: '/settings/system/general', labelKey: 'gui.settings.system_nav.general', icon: 'gear', groupKey: 'gui.settings.system_groups.ops' },
  { key: 'agents', scope: 'system', requires: SYSTEM_READ, routeName: 'settings-system-agents', path: '/settings/system/agents', labelKey: 'gui.settings.system_nav.agents', icon: 'bot', groupKey: 'gui.settings.system_groups.resources' },
  { key: 'voice', scope: 'system', requires: SYSTEM_READ, routeName: 'settings-system-voice', path: '/settings/system/voice', labelKey: 'gui.settings.system_nav.voice', icon: 'mic', groupKey: 'gui.settings.system_groups.resources' },
  { key: 'users', scope: 'system', requires: { scope: 'system', actions: ['system.user.read'] }, routeName: 'settings-system-users', path: '/settings/system/users', labelKey: 'gui.settings.users.title', icon: 'users', groupKey: 'gui.settings.system_groups.accounts' },
  { key: 'projects', scope: 'system', requires: SYSTEM_READ, routeName: 'settings-system-projects', path: '/settings/system/projects', labelKey: 'gui.settings.system_nav.projects', icon: 'folder', groupKey: 'gui.settings.system_groups.accounts' },
]

/** The settings pages the signed-in person may open from a menu right now, in menu order. */
export function useSettingsMenu() {
  const router = useRouter()
  const { user, status } = useCurrentUser()
  const lists = useAccessLists()

  // The workspace and project capabilities are read once, the first time a menu needs them (only
  // when the server reports capabilities at all).
  watchEffect(() => {
    if (status.value === 'ready' && reportsCapabilities(user.value) && lists.status.value === 'idle') void loadSettingsAccess()
  })

  return computed<SettingsPage[]>(() => {
    const person = status.value === 'ready' ? user.value : null
    const modern = reportsCapabilities(person)
    const snapshot = { status: lists.status.value, workspaces: lists.workspaces.value, projects: lists.projects.value }
    const pages = SETTINGS_PAGES.filter(
      (page) =>
        router.hasRoute(page.routeName)
        && (!page.modern || modern)
        && (!page.requires || evaluateRequirement(page.requires, person, snapshot) === 'allow'),
    )
    // With one workspace (and no way to create another) the 워크스페이스 entry is the "워크스페이스 설정"
    // screen itself, no list in between.
    const single =
      snapshot.status === 'ready' && snapshot.workspaces.length === 1 && !allows(person?.capabilities, 'system.workspace.create')
        ? snapshot.workspaces[0]
        : null
    return pages.map((page) =>
      page.key === 'workspace' && single
        ? { ...page, path: `/settings/workspaces/${encodeURIComponent(single.workspace_id)}`, labelKey: 'gui.settings.manage_nav.workspace_settings' }
        : page,
    )
  })
}
