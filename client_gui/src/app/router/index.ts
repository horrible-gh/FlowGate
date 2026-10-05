import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { installAuthGuard } from '@/core/router/guard'
import { AiSettingsView } from '@/features/ai'
import { DesignerShell } from '@/features/designer'
import {
  SettingsHubView,
  SystemSecurityView,
  SystemUsersView,
  WorkspaceDetailView,
  WorkspaceListView,
} from '@/features/settings'
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
// /app/settings is 환경 설정 (flowgate.v02.0020 시안 11~14): the
// scope hub, 워크스페이스 목록/상세 and 시스템 › 사용자 / 보안; any other /settings path keeps the
// section highlighted and renders NotFoundView in place.
//
// Nothing here redirects: a `redirect` rewrites the URL bar, and the T#1 contract is that an
// authenticated deep link (e.g. `/app/projects?tab=x`) and a browser refresh keep exactly the
// URL that was requested. Unknown paths render NotFoundView in place for the same reason.
const placeholderRoutes: RouteRecordRaw[] = PLACEHOLDER_NAV_KEYS.map((key) => ({
  path: `/${key}/:rest(.*)*`,
  name: key,
  component: ComingSoonView,
  meta: { navKey: key },
}))

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'dashboard', component: DashboardView, meta: { navKey: 'dashboard' } },
  { path: '/ai/:rest(.*)*', name: 'ai', component: AiSettingsView, meta: { navKey: 'ai' } },
  { path: '/designer/:rest(.*)*', name: 'designer', component: DesignerShell, meta: { navKey: 'designer', fullscreen: true } },
  { path: '/settings', name: 'settings', component: SettingsHubView, meta: { navKey: 'settings' } },
  { path: '/settings/workspaces', name: 'settings-workspaces', component: WorkspaceListView, meta: { navKey: 'settings' } },
  {
    path: '/settings/workspaces/:workspaceId',
    name: 'settings-workspace',
    component: WorkspaceDetailView,
    props: true,
    meta: { navKey: 'settings' },
  },
  { path: '/settings/system/users', name: 'settings-system-users', component: SystemUsersView, meta: { navKey: 'settings' } },
  { path: '/settings/system/security', name: 'settings-system-security', component: SystemSecurityView, meta: { navKey: 'settings' } },
  { path: '/settings/:rest(.*)*', name: 'settings-not-found', component: NotFoundView, meta: { navKey: 'settings' } },
  ...placeholderRoutes,
  { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView },
]

const router = createRouter({
  history: createWebHistory('/app'),
  routes,
})

installAuthGuard(router)

export default router