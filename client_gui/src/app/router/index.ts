import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { installAuthGuard } from '@/core/router/guard'
import { AiSettingsView } from '@/features/ai'
import { DesignerShell } from '@/features/designer'
import { installSettingsAccessGuard, SettingsLayout, SETTINGS_UNKNOWN_ROUTE } from '@/features/settings/routing'
import DashboardView from '../dashboard/DashboardView.vue'
import { PLACEHOLDER_NAV_KEYS } from '../navigation'
import ComingSoonView from '../views/ComingSoonView.vue'
import NotFoundView from '../views/NotFoundView.vue'

// T0011 §8/§12: /app is the Dashboard; /app/projects and /git are honest
// "준비 중" placeholders. Each placeholder also owns everything below it (`/projects/...`),
// so a deep link keeps its section highlighted in the Sidebar.
// /app/ai is implemented by AiSettingsView (T0003), including 시스템 AI / 내 AI
// (flowgate.v02.0017 T0004 §1). /app/designer (and everything below it) is the Designer
// Shell (flowgate.v02.0025); sub-paths are not interpreted yet, they keep the same Shell.
// `meta.fullscreen` makes App.vue drop its Topbar / Sidebar for the Designer (0025 T0006 §1).
//
// Nothing outside /settings redirects: a `redirect` rewrites the URL bar, and the T#1 contract
// is that an authenticated deep link (e.g. `/app/projects?tab=x`) and a browser refresh keep
// exactly the URL that was requested. Unknown paths render NotFoundView in place for the same
// reason.
const placeholderRoutes: RouteRecordRaw[] = PLACEHOLDER_NAV_KEYS.map((key) => ({
  path: `/${key}/:rest(.*)*`,
  name: key,
  component: ComingSoonView,
  meta: { navKey: key },
}))

// /app/settings is 환경 설정 (flowgate.v02.0076 T#1, D §1.2): every screen is a lazily loaded
// child of SettingsLayout. `meta.settingsAccess: 'admin'` marks the 프로젝트/시스템 screens that only
// a system administrator is shown (the access guard also treats every /settings/project… and
// /settings/system… path that way). 내 설정's me/account and me/security (T#2) are open to every
// signed-in person. 시스템 › 일반 (system/general, T#3) and 사용자 are administrator screens. D §1.2's
// other URLs — system/agents, system/projects, project and project/:projectId/{general,paths,git} —
// are registered by the steps that build their views; until then they, the retired
// /settings/workspaces[/…] and /settings/system/security, and any other unknown /settings/* path
// are replaced by the overview (installSettingsAccessGuard). The settings menus list a screen
// only once its route below exists.
const settingsRoute: RouteRecordRaw = {
  path: '/settings',
  component: SettingsLayout,
  meta: { navKey: 'settings' },
  children: [
    { path: '', name: 'settings', component: () => import('@/features/settings/views/SettingsHubView.vue') },
    {
      path: 'me/account',
      name: 'settings-me-account',
      component: () => import('@/features/settings/views/MeAccountSettingsView.vue'),
    },
    {
      path: 'me/security',
      name: 'settings-me-security',
      component: () => import('@/features/settings/views/MeSecuritySettingsView.vue'),
    },
    {
      path: 'system/general',
      name: 'settings-system-general',
      component: () => import('@/features/settings/views/SystemGeneralSettingsView.vue'),
      meta: { settingsAccess: 'admin' },
    },
    {
      path: 'system/users',
      name: 'settings-system-users',
      component: () => import('@/features/settings/views/SystemUsersView.vue'),
      meta: { settingsAccess: 'admin' },
    },
    // Never rendered: the access guard replaces every path it matches by the overview.
    { path: ':rest(.*)*', name: SETTINGS_UNKNOWN_ROUTE, component: NotFoundView },
  ],
}

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'dashboard', component: DashboardView, meta: { navKey: 'dashboard' } },
  { path: '/ai/:rest(.*)*', name: 'ai', component: AiSettingsView, meta: { navKey: 'ai' } },
  { path: '/designer/:rest(.*)*', name: 'designer', component: DesignerShell, meta: { navKey: 'designer', fullscreen: true } },
  settingsRoute,
  ...placeholderRoutes,
  { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView },
]

const router = createRouter({
  history: createWebHistory('/app'),
  routes,
})

installAuthGuard(router)
installSettingsAccessGuard(router)

export default router
