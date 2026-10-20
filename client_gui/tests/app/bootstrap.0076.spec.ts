import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import type { SystemUsersResponse } from '@/features/settings/types'

// flowgate.v02.0076 T#1 (D §1.2, §5.2 V03): the real start of /app. src/app/main.ts runs as the
// browser runs it (token recovery, /auth/me, router install, router.isReady, mount on #app).
// Only the transport (@shared/api) is replaced; it records every request and holds /auth/me
// until the test answers it, so "응답 전" is the state a direct entry or a refresh really shows.

type Deferred = { resolve: (value: unknown) => void; reject: (error: unknown) => void }

const net = vi.hoisted(() => ({
  requests: [] as string[],
  me: null as null | Deferred,
  users: null as unknown,
}))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const record = (method: string) =>
    vi.fn(async (path: string, params?: Record<string, string>) => {
      const query = method === 'GET' && params && Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : ''
      net.requests.push(`${method} ${path}${query}`)
      if (method === 'GET' && path === '/auth/me') {
        return new Promise((resolve, reject) => {
          net.me = { resolve, reject }
        })
      }
      if (method === 'GET' && path === '/api/v1/system/users') return { data: net.users }
      if (method === 'GET' && path === '/api/v1/system/agents') return { data: { agents: [] } }
      if (method === 'GET' && path === '/api/v1/projects' && query === '?status=all') {
        return { data: { projects: [{ project_id: 'p1', project_name: 'Alpha', description: null, is_active: 1, modules: [] }] } }
      }
      if (method === 'GET' && path === '/api/v1/projects/p1/storage-root') {
        return { data: { project_id: 'p1', configured_value: null, project_override: null, effective_value: '/srv/flowgate', effective_source: 'default', env_override: false } }
      }
      if (method === 'GET' && path === '/api/v1/projects/p1/git/config') return { data: { ok: true, configured: false, config: null } }
      throw Object.assign(new Error(`unexpected request in test: ${method} ${path}`), { response: { status: 500 } })
    })
  return {
    ...actual,
    ensureValidAccessToken: vi.fn(async () => true),
    startTokenAutoRefresh: vi.fn(),
    getRequest: record('GET'),
    postRequest: record('POST'),
    patchRequest: record('PATCH'),
    putRequest: record('PUT'),
    deleteRequest: record('DELETE'),
  }
})

const ADMIN_ME = { username: 'admin', email: 'admin@flowgate.local', is_admin: true }
const P_ME = { username: 'p-admin', email: null, is_admin: false }

const LOADING_URLS = ['/settings/system/agents', '/settings/system/projects', '/settings/project/p1/paths', '/settings/project/p1/git', '/settings/system/users']

function users(): SystemUsersResponse {
  return {
    summary: { total: 1, active: 1, invited: 0, disabled: 0, admins: 1, tfa_missing: 0, tfa_enabled: 1, no_workspace: 0 },
    workspaces: [],
    users: [
      { user_id: 'u1', username: 'admin', email: 'admin@flowgate.local', is_admin: true, state: 'active', tfa_enabled: true, workspaces: [], last_login_at: null, created_at: '2026-03-02T00:00:00+00:00' },
    ],
  }
}

const $ = (selector: string) => document.querySelector(selector)
const location = () => window.location.pathname + window.location.search + window.location.hash
// Requests an administrator settings screen or the project picker would make.
const managementRequests = () => net.requests.filter((r) => /\/(system|projects?|settings)(\/|\?|$)/.test(r.split(' ')[1] ?? ''))

const FORM_CONTROLS = ['form', 'input', 'select', 'textarea', 'table']
const controlsIn = (root: string) => document.querySelectorAll(FORM_CONTROLS.map((tag) => `${root} ${tag}`).join(', '))

// The routed screen only: the App Shell's own Topbar has a search input of its own.
function expectNoManagementDom(): void {
  expect($('[data-panel="users"]')).toBeNull()
  expect($('[data-panel="system-agents"]')).toBeNull()
  expect($('[data-panel="system-projects"]')).toBeNull()
  expect($('[data-panel^="project-"]')).toBeNull()
  expect($('.settings-shell-view')).toBeNull()
  expect(controlsIn('.gui-route-content')).toHaveLength(0)
}

async function waitUntil(done: () => boolean): Promise<void> {
  for (let i = 0; i < 400 && !done(); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10))
    await flushPromises()
  }
  await flushPromises()
}

/** Open `/app${url}` in a fresh page: a direct entry or a browser refresh. */
async function startApp(url: string): Promise<Router> {
  vi.resetModules()
  document.body.innerHTML = '<div id="app"></div>'
  window.history.replaceState({}, '', `/app${url}`)
  await import('@/app/main')
  const router = (await import('@/app/router/index')).default
  await flushPromises()
  return router
}

/** While /auth/me is out: the neutral loading state only — no route, no shell, no view, no other request. */
function expectStartupLoadingOnly(router: Router, url: string): void {
  const loading = $('#app [data-testid="app-startup-loading"]')
  expect(loading).not.toBeNull()
  expect(loading!.querySelector('[role="status"]')).not.toBeNull()
  expect(loading!.textContent?.trim()).not.toBe('')
  expect(loading!.textContent).not.toContain('gui.common')
  expect(location()).toBe(`/app${url}`)
  expect(router.currentRoute.value.matched).toHaveLength(0) // the router has not resolved anything yet
  expect($('.gui-app-shell')).toBeNull()
  expect($('.settings-hub')).toBeNull()
  expectNoManagementDom()
  expect(controlsIn('#app')).toHaveLength(0)
  expect(net.requests).toEqual(['GET /auth/me'])
}

beforeEach(() => {
  sessionStorage.clear()
  delete window.__accessToken__
  sessionStorage.setItem('fg_access_token', 'token-bootstrap-0076')
  net.requests.length = 0
  net.me = null
  net.users = users()
})

afterEach(() => {
  const host = $('#app') as (Element & { __vue_app__?: { unmount: () => void } }) | null
  host?.__vue_app__?.unmount()
  document.body.innerHTML = ''
  sessionStorage.clear()
  delete window.__accessToken__
})

describe('V03 실제 초기 진입 — main.ts shows a neutral loading state until /auth/me answers', () => {
  it.each(LOADING_URLS)('%s, answer P → overview with the 접근 불가 notice; no system/project request', async (url) => {
    const router = await startApp(url)
    expectStartupLoadingOnly(router, url)

    net.me!.resolve({ data: P_ME })
    await waitUntil(() => $('.settings-hub') !== null)
    expect(router.currentRoute.value.fullPath).toBe('/settings')
    expect(location()).toBe('/app/settings')
    expect($('[data-testid="app-startup-loading"]')).toBeNull()
    expect($('.gui-app-shell .settings-hub')).not.toBeNull()
    expect($('[data-testid="settings-access-notice"]')).not.toBeNull()
    expectNoManagementDom()
    expect(managementRequests()).toEqual([])
  }, 15000)

  it('/settings/system/users, answer A → the view mounts and starts its GET only after the answer', async () => {
    const router = await startApp('/settings/system/users')
    expectStartupLoadingOnly(router, '/settings/system/users')

    net.me!.resolve({ data: ADMIN_ME })
    await waitUntil(() => $('[data-panel="users"]') !== null)
    expect(router.currentRoute.value.fullPath).toBe('/settings/system/users')
    expect(location()).toBe('/app/settings/system/users')
    expect($('[data-testid="app-startup-loading"]')).toBeNull()
    expect($('.gui-app-shell [data-panel="users"]')).not.toBeNull()
    expect(net.requests[0]).toBe('GET /auth/me')
    expect(managementRequests()).toEqual(['GET /api/v1/system/users'])
  }, 15000)

  // flowgate.v02.0076 T#4 registered 시스템 Agent: like 사용자, its view and its one GET start only now.
  it('/settings/system/agents, answer A → the view mounts and starts its GET only after the answer', async () => {
    const router = await startApp('/settings/system/agents')
    expectStartupLoadingOnly(router, '/settings/system/agents')

    net.me!.resolve({ data: ADMIN_ME })
    await waitUntil(() => $('[data-panel="system-agents"]') !== null)
    expect(router.currentRoute.value.fullPath).toBe('/settings/system/agents')
    expect(location()).toBe('/app/settings/system/agents')
    expect($('[data-testid="app-startup-loading"]')).toBeNull()
    expect($('.gui-app-shell [data-panel="system-agents"]')).not.toBeNull()
    expect(net.requests[0]).toBe('GET /auth/me')
    expect(managementRequests()).toEqual(['GET /api/v1/system/agents'])
  }, 15000)

  // flowgate.v02.0076 T#5 registered 전체 프로젝트 and the 프로젝트 sections: the same rule — the view,
  // the project list GET and then that project's one GET start only after the answer.
  it.each([
    ['/settings/system/projects', '[data-panel="system-projects"]', ['GET /api/v1/projects?status=all']],
    ['/settings/project/p1/paths', '[data-panel="project-paths"]', ['GET /api/v1/projects?status=all', 'GET /api/v1/projects/p1/storage-root']],
    ['/settings/project/p1/git', '[data-panel="project-git"]', ['GET /api/v1/projects?status=all', 'GET /api/v1/projects/p1/git/config']],
  ])('%s, answer A → the view mounts and starts its GETs only after the answer', async (url, panel, calls) => {
    const router = await startApp(url)
    expectStartupLoadingOnly(router, url)

    net.me!.resolve({ data: ADMIN_ME })
    await waitUntil(() => $(panel) !== null)
    expect(router.currentRoute.value.fullPath).toBe(url)
    expect(location()).toBe(`/app${url}`)
    expect($('[data-testid="app-startup-loading"]')).toBeNull()
    expect($(`.gui-app-shell ${panel}`)).not.toBeNull()
    expect(net.requests[0]).toBe('GET /auth/me')
    expect(managementRequests()).toEqual(calls)
  }, 15000)

  it('outside /settings the requested URL is kept as before (/projects?tab=x)', async () => {
    const router = await startApp('/projects?tab=x')
    expectStartupLoadingOnly(router, '/projects?tab=x')

    net.me!.resolve({ data: ADMIN_ME })
    await waitUntil(() => $('.gui-app-shell') !== null)
    expect(router.currentRoute.value.fullPath).toBe('/projects?tab=x')
    expect(location()).toBe('/app/projects?tab=x')
    expect($('[data-testid="app-startup-loading"]')).toBeNull()
  }, 15000)
})

describe('login redirect contract is unchanged', () => {
  const realLocation = window.location
  let redirectedTo = ''

  // jsdom cannot leave the page, so window.location becomes a plain object that records the
  // guard's `location.href = '/login?...'` (same technique as tests/core/router.guard.spec.ts).
  // It keeps protocol/host so createWebHistory still writes same-origin history entries.
  function fakeLocation(url: string): void {
    redirectedTo = ''
    const fake = {
      protocol: realLocation.protocol,
      host: realLocation.host,
      pathname: `/app${url}`,
      search: '',
      hash: '',
      get href() {
        return redirectedTo
      },
      set href(value: string) {
        redirectedTo = value
      },
    }
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: unknown }).location = fake
  }

  async function startWithFakeLocation(url: string): Promise<void> {
    fakeLocation(url)
    vi.resetModules()
    document.body.innerHTML = '<div id="app"></div>'
    await import('@/app/main')
    await flushPromises()
  }

  afterEach(() => {
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: Location }).location = realLocation
  })

  it('an invalid stored token: loading while /auth/me is out, then /login?next=… and nothing mounts', async () => {
    await startWithFakeLocation('/settings/system/users')
    expect($('#app [data-testid="app-startup-loading"]')).not.toBeNull()
    expect(net.requests).toEqual(['GET /auth/me'])

    net.me!.reject(Object.assign(new Error('401'), { response: { status: 401 } }))
    await waitUntil(() => redirectedTo !== '')
    expect(redirectedTo).toBe(`/login?next=${encodeURIComponent('/app/settings/system/users')}`)
    expect(sessionStorage.getItem('fg_access_token')).toBeNull()
    expect(window.__accessToken__).toBeUndefined()
    expect($('.gui-app-shell')).toBeNull()
    expectNoManagementDom()
    expect(net.requests).toEqual(['GET /auth/me'])
  }, 15000)

  it('no stored token: no /auth/me, no loading state, straight to /login?next=…', async () => {
    sessionStorage.clear()
    await startWithFakeLocation('/settings/system/users')
    await waitUntil(() => redirectedTo !== '')
    expect(redirectedTo).toBe(`/login?next=${encodeURIComponent('/app/settings/system/users')}`)
    expect($('[data-testid="app-startup-loading"]')).toBeNull()
    expect($('.gui-app-shell')).toBeNull()
    expect(net.requests).toEqual([])
  }, 15000)
})
