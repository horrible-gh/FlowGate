import { createRouter, createWebHistory } from 'vue-router'
import { installAuthGuard } from '@/core/router/guard'
import DashboardPlaceholderView from '../views/DashboardPlaceholderView.vue'

// T#1 scope only (T0005 §7): a single placeholder route to prove /app bootstrap,
// routing, and auth guard work end to end. Real screens (Project/Designer/Git/AI/
// Environment/Dashboard) are out of scope until T#2 and later.
//
// The catch-all RENDERS the placeholder directly instead of `redirect: '/'`: a
// `redirect` rewrites the URL bar, so an authenticated deep-link like
// `/app/projects?tab=x` would flash to `/app/` the instant the router resolved it —
// breaking the "복귀 경로 그대로 유지" contract (T0005 §5/§9) for every path except the
// bare root. Matching the same component on both routes keeps the requested /app/*
// URL intact while T#1's placeholder still satisfies "직접 접근 시 안전하게 처리".
const routes = [
  {
    path: '/',
    component: DashboardPlaceholderView,
  },
  { path: '/:pathMatch(.*)*', component: DashboardPlaceholderView },
]

const router = createRouter({
  history: createWebHistory('/app'),
  routes,
})

installAuthGuard(router)

export default router
