import { createRouter, createWebHistory } from 'vue-router'
import type { RouteLocationNormalized } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import RequirementCreateView from '../views/RequirementCreateView.vue'
import { isAdminFromToken } from '@shared/auth'

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

router.beforeEach((to) => {
  let token = window.__accessToken__
  if (!token) {
    token = sessionStorage.getItem('fg_access_token') ?? undefined
    if (token) window.__accessToken__ = token
  }
  if (to.meta.requiresAuth && !token) {
    window.location.href = '/index.html'
    return false
  }
  if (to.meta.requiresAdmin && !isAdminFromToken()) {
    return { path: '/' }
  }
  return true
})

export default router
