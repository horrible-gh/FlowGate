import type { Router } from 'vue-router'

// core owns router infrastructure (T0005 §4); app only supplies the route table.

const getStoredToken = (): string | null =>
  window.__accessToken__ || sessionStorage.getItem('fg_access_token') || null

/**
 * Minimum viable route guard for T#1: block navigation while unauthenticated and send
 * the browser to the shared /login entry point with `next` set to the exact /app URL
 * that was requested, so a successful login can return here (T0005 §5).
 *
 * This reuses the SAME token-presence check client/src/main/router/index.ts already
 * uses for /main (window.__accessToken__ / sessionStorage 'fg_access_token') rather
 * than inventing a second auth-state convention.
 */
export function installAuthGuard(router: Router): void {
  router.beforeEach(() => {
    if (getStoredToken()) return true
    const next = `${window.location.pathname}${window.location.search}`
    window.location.href = `/login?next=${encodeURIComponent(next)}`
    return false
  })
}
