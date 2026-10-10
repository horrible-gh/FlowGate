import { createRouter, createWebHistory } from 'vue-router'
import type { RouteLocationNormalized } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import RequirementCreateView from '../views/RequirementCreateView.vue'
import { isAdminFromToken, resolveSafeReturnPath } from '@shared/auth'

const routes = [
  {
    path: '/',
    component: DashboardView,
    meta: { requiresAuth: true },
  },
  {
    path: '/projects',
    component: { template: '<div />' },
    meta: { requiresAuth: true, requiresAdmin: true },
    beforeEnter: (to: RouteLocationNormalized) => {
      const query = to.fullPath.includes('?') ? to.fullPath.slice(to.fullPath.indexOf('?')) : ''
      window.location.href = `/settings/projects${query}`
      return false
    },
  },
  {
    path: '/requirements/create',
    component: RequirementCreateView,
    meta: { requiresAuth: true },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory('/main'),
  routes,
})

/**
 * flowgate.v02.0048 T#4 (D#1 §9.4): where a /main navigation without a session goes — the shared
 * /login with `next` set to the /main URL that was asked for (e.g. a `/main/?doc=<doc_id>` deep
 * link), so a successful login returns there (`authRedirect.toDashboard()`). Same shape as the
 * v0.2 `installAuthGuard`; `next` is only ever a value `resolveSafeReturnPath` accepts, otherwise
 * plain /login.
 */
export function loginRedirectFor(target: string): string {
  const next = resolveSafeReturnPath(target)
  return next ? `/login?next=${encodeURIComponent(next)}` : '/login'
}

router.beforeEach((to) => {
  let token = window.__accessToken__
  if (!token) {
    token = sessionStorage.getItem('fg_access_token') ?? undefined
    if (token) window.__accessToken__ = token
  }
  if (to.meta.requiresAuth && !token) {
    // `href` carries the /main base: `/main/?doc=…`.
    window.location.href = loginRedirectFor(router.resolve(to).href)
    return false
  }
  if (to.meta.requiresAdmin && !isAdminFromToken()) {
    return { path: '/' }
  }
  return true
})

export default router
