import { beforeEach, describe, expect, it, vi } from 'vitest'

// Real router round-trip (not the fake-router stub tests/core/router.guard.spec.ts uses):
// proves the actual createWebHistory('/app') router + installAuthGuard combination
// preserves a requested /app/* deep-link URL instead of rewriting it to '/app/' — the
// regression the review flagged in the previous catch-all (`redirect: '/'`).

async function loadRouterFresh() {
  vi.resetModules()
  const mod = await import('@/app/router/index')
  return mod.default
}

describe('/app router deep-link round trip', () => {
  beforeEach(() => {
    sessionStorage.clear()
    delete window.__accessToken__
    window.history.replaceState({}, '', '/app/')
  })

  it('renders the placeholder for an authenticated deep /app/* path without rewriting the URL', async () => {
    window.__accessToken__ = 'token-deep-link'
    const router = await loadRouterFresh()

    // `router.push` resolves once navigation (guards included) finishes — no need for
    // `app.use(router)`, which is the only thing that would otherwise kick off vue-router's
    // own initial navigation and settle `isReady()`.
    await router.push('/projects?tab=x')

    expect(router.currentRoute.value.fullPath).toBe('/projects?tab=x')
    expect(window.location.pathname + window.location.search).toBe('/app/projects?tab=x')
    // T0011: resolved to the /app/projects placeholder route (not the '/' Dashboard and
    // not the not-found catch-all).
    expect(router.currentRoute.value.name).toBe('projects')
  })

  it('preserves a nested deep path for refresh-style direct navigation identically', async () => {
    window.__accessToken__ = 'token-deep-link'
    window.history.replaceState({}, '', '/app/projects/nested/refresh')
    const router = await loadRouterFresh()

    // Mirrors what vue-router's own `install(app)` does on a real page load: navigate
    // to whatever the browser's current URL already is (this is what a browser refresh
    // on a deep /app/* path re-requests from the server, which serves this same SPA shell).
    await router.push('/projects/nested/refresh')

    expect(router.currentRoute.value.fullPath).toBe('/projects/nested/refresh')
    expect(window.location.pathname).toBe('/app/projects/nested/refresh')
    // A path below a section stays in that section (Sidebar keeps it highlighted).
    expect(router.currentRoute.value.name).toBe('projects')
  })
})
