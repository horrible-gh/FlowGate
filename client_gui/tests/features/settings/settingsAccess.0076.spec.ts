import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterView } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import router from '@/app/router/index'
import { loadCurrentUser, setCurrentUser } from '@/core/auth/currentUser'
import { decideSettingsAccess } from '@/features/settings/access'
import type { SystemUsersResponse } from '@/features/settings/types'

// flowgate.v02.0076 T#1 (D §1.2, §5.2 V01–V04): the real /app router — its settings routes,
// installAuthGuard and installSettingsAccessGuard — with SettingsLayout and the real settings
// views. Only the transport (@shared/api) is replaced, and it records every request, so "API
// 요청 0" is checked on the request list itself rather than on a mocked view.

const net = vi.hoisted(() => ({
  requests: [] as string[],
  me: null as null | { resolve: (value: unknown) => void; reject: (error: unknown) => void },
  users: null as unknown,
  root: null as unknown,
  settings: null as unknown,
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
      if (method === 'GET' && path === '/api/v1/system/storage-root') return { data: net.root }
      if (method === 'GET' && path === '/api/v1/system/settings') return { data: net.settings }
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
    getRequest: record('GET'),
    postRequest: record('POST'),
    patchRequest: record('PATCH'),
    putRequest: record('PUT'),
    deleteRequest: record('DELETE'),
  }
})

registerAppMessages(i18n)

const Host = defineComponent({ render: () => h(RouterView) })

const ADMIN = { username: 'admin', email: 'admin@flowgate.local', isAdmin: true }
// P / M / N / D: project admin, member, non-member and an account with delegated system
// permissions. The GUI only ever sees /auth/me's is_admin, which is false for all four.
const NON_ADMINS = [
  ['P', { username: 'p-admin', email: null, isAdmin: false }],
  ['M', { username: 'member', email: null, isAdmin: false }],
  ['N', { username: 'outsider', email: null, isAdmin: false }],
  ['D', { username: 'delegated', email: null, isAdmin: false }],
] as const

const ADMIN_URLS = [
  '/settings/system/general',
  '/settings/system/agents',
  '/settings/system/users',
  '/settings/system/projects',
  '/settings/project',
  '/settings/project/p1/general',
  '/settings/project/p1/paths',
  '/settings/project/p1/git',
]

const RETIRED_URLS = [
  '/settings/workspaces',
  '/settings/workspaces/w1?tab=policy#members',
  '/settings/system/security',
]

function users(): SystemUsersResponse {
  return {
    summary: { total: 1, active: 1, invited: 0, disabled: 0, admins: 1, tfa_missing: 0, tfa_enabled: 1, no_workspace: 0 },
    workspaces: [],
    users: [
      { user_id: 'u1', username: 'admin', email: 'admin@flowgate.local', is_admin: true, state: 'active', tfa_enabled: true, workspaces: [], last_login_at: null, created_at: '2026-03-02T00:00:00+00:00' },
    ],
  }
}

let wrapper: VueWrapper | null = null

function mountHost(): VueWrapper {
  wrapper = mount(Host, { global: { plugins: [i18n, router], stubs: { teleport: true } } })
  return wrapper
}

/** Direct entry / refresh: the browser already shows the URL and the router resolves it. */
async function enterDirectly(url: string): Promise<VueWrapper> {
  window.history.replaceState({}, '', `/app${url}`)
  await router.replace(url)
  const w = mountHost()
  await flushPromises()
  return w
}

const location = () => window.location.pathname + window.location.search + window.location.hash
const hrefs = (w: VueWrapper) => w.findAll('a[href]').map((a) => a.attributes('href') ?? '')
const settingsRequests = () => net.requests.filter((r) => r !== 'GET /auth/me')

function expectNoManagementDom(w: VueWrapper): void {
  expect(w.find('[data-panel="users"]').exists()).toBe(false)
  expect(w.find('[data-panel="system-general"]').exists()).toBe(false)
  expect(w.find('[data-panel="system-agents"]').exists()).toBe(false)
  expect(w.find('[data-panel="system-projects"]').exists()).toBe(false)
  expect(w.find('[data-panel^="project-"]').exists()).toBe(false)
  expect(w.find('.settings-shell-view').exists()).toBe(false)
  expect(w.findAll('form, input, select, textarea, table').length).toBe(0)
}

function expectNoRetiredOrInertEntries(w: VueWrapper): void {
  for (const href of hrefs(w)) {
    expect(href).not.toMatch(/\/settings\/(workspaces|system\/security)/)
  }
  expect(w.find('[data-scope-card="workspace"], [data-scope="workspace"]').exists()).toBe(false)
  for (const key of ['storage', 'environment', 'logs', 'security', 'info', 'workspace']) {
    expect(w.find(`[data-subnav="${key}"]`).exists()).toBe(false)
  }
  expect(w.find('.disabled, [aria-disabled="true"], button[disabled]').exists()).toBe(false)
  expect(w.text()).not.toContain('준비 중')
}

async function waitForRoute(fullPath: string): Promise<void> {
  for (let i = 0; i < 50 && router.currentRoute.value.fullPath !== fullPath; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10))
    await flushPromises()
  }
  await flushPromises()
}

beforeEach(async () => {
  i18n.global.locale.value = 'ko'
  sessionStorage.clear()
  window.__accessToken__ = 'token-settings-0076'
  net.users = users()
  net.root = { project_id: null, configured_value: null, project_override: null, effective_value: '/srv/flowgate', effective_source: 'default', env_override: false }
  net.settings = { settings: [
    { setting_key: 'ai_repeat_count_max', setting_value: '3', value_type: 'integer' },
    { setting_key: 'scratch_ttl_unit', setting_value: 'day', value_type: 'string' },
    { setting_key: 'scratch_ttl_value', setting_value: '7', value_type: 'integer' },
  ] }
  net.me = null
  setCurrentUser(null)
  window.history.replaceState({}, '', '/app/')
  await router.replace('/')
  net.requests.length = 0
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
  delete window.__accessToken__
})

describe('V01 메뉴 — hub, scope tabs and 시스템 sub-navigation', () => {
  it('A: 내 설정 2개, 프로젝트 선택, 시스템 › 일반 · 시스템 Agent · 사용자 · 전체 프로젝트; no Workspace/retired/"준비 중" entry', async () => {
    setCurrentUser(ADMIN)
    const hub = await enterDirectly('/settings')
    expect(hub.findAll('[data-scope-card]').map((c) => c.attributes('data-scope-card'))).toEqual(['me', 'project', 'system'])
    expect(hub.get('[data-scope-card="project"] a').attributes('href')).toBe('/app/settings/project')
    expect(hub.get('[data-scope-card="project"]').findAll('.tag').map((t) => t.text())).toEqual(['프로젝트 선택'])
    expect(hub.get('[data-scope-card="me"] a').attributes('href')).toBe('/app/settings/me/account')
    expect(hub.get('[data-scope-card="me"]').findAll('.tag').map((t) => t.text())).toEqual(['계정 · 화면', '보안 · 세션'])
    expect(hub.get('[data-scope-card="system"] a').attributes('href')).toBe('/app/settings/system/general')
    expect(hub.get('[data-scope-card="system"]').findAll('.tag').map((t) => t.text())).toEqual(['일반', '시스템 Agent', '사용자', '전체 프로젝트'])
    expectNoRetiredOrInertEntries(hub)
    expect(settingsRequests()).toEqual([]) // the overview loads no page data
    hub.unmount()

    const page = await enterDirectly('/settings/system/users')
    expect(page.findAll('[data-scope]').map((t) => t.attributes('data-scope'))).toEqual(['me', 'project', 'system'])
    expect(page.findAll('.subnav [data-subnav]').map((i) => i.attributes('data-subnav'))).toEqual(['general', 'agents', 'users', 'projects'])
    expect(page.find('.subnav-group').exists()).toBe(false)
    for (const href of hrefs(page)) expect(href).not.toMatch(/\/settings\/(workspaces|system\/security)/)
    for (const key of ['storage', 'environment', 'logs', 'security', 'info', 'workspace']) {
      expect(page.find(`[data-subnav="${key}"]`).exists()).toBe(false)
    }
  })

  it.each(NON_ADMINS)('%s: 내 설정 2개 only — no 프로젝트 / 전체 프로젝트 / 시스템 link, no disabled card', async (_label, person) => {
    setCurrentUser(person)
    const hub = await enterDirectly('/settings')
    expect(hub.findAll('[data-scope-card]').map((c) => c.attributes('data-scope-card'))).toEqual(['me'])
    expect(hub.get('[data-scope-card="me"]').findAll('.tag').map((t) => t.text())).toEqual(['계정 · 화면', '보안 · 세션'])
    expect(hrefs(hub).filter((href) => /\/settings\/(system|project)/.test(href))).toEqual([])
    expect(hub.find('[data-testid="settings-hub-empty"]').exists()).toBe(false)
    expectNoRetiredOrInertEntries(hub)
    expect(settingsRequests()).toEqual([])
  })
})

describe('V02 비관리자 direct URL — replaced by the overview before any view mounts', () => {
  for (const [label, person] of NON_ADMINS) {
    it.each(ADMIN_URLS)(`${label}: %s → /app/settings with the 접근 불가 notice, 0 requests`, async (url) => {
      setCurrentUser(person)
      const w = await enterDirectly(url)
      expect(router.currentRoute.value.fullPath).toBe('/settings')
      expect(location()).toBe('/app/settings')
      expect(w.get('[data-testid="settings-access-notice"]').text()).toContain('시스템 관리자만 열 수 있는 화면')
      expectNoManagementDom(w)
      expect(net.requests).toEqual([])
    })
  }

  it('an in-app move to an admin URL is refused the same way (case-insensitive path)', async () => {
    setCurrentUser(NON_ADMINS[0][1])
    const w = await enterDirectly('/settings')
    await router.push('/settings/System/Users')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings')
    expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(true)
    expectNoManagementDom(w)
    expect(net.requests).toEqual([])
  })

  it('the notice is shown once: leaving and coming back to the overview drops it', async () => {
    setCurrentUser(NON_ADMINS[0][1])
    const w = await enterDirectly('/settings/system/agents')
    expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(true)
    await router.push('/projects')
    await router.push('/settings')
    await flushPromises()
    expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(false)
  })
})

describe('V03 권한 로딩 — neutral loading only until /auth/me answers', () => {
  const LOADING_URLS = ['/settings/system/agents', '/settings/system/projects', '/settings/project/p1/paths', '/settings/project/p1/git', '/settings/system/users', '/settings/system/general']

  async function enterWhileLoading(url: string): Promise<VueWrapper> {
    void loadCurrentUser().catch(() => undefined)
    window.history.replaceState({}, '', `/app${url}`)
    await router.replace(url)
    const w = mountHost()
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe(url)
    expect(w.find('[data-testid="settings-loading"]').exists()).toBe(true)
    expect(w.find('.settings-hub').exists()).toBe(false)
    expectNoManagementDom(w)
    expect(net.requests).toEqual(['GET /auth/me'])
    return w
  }

  it.each(LOADING_URLS)('%s, answer P → overview with notice; no system/project request', async (url) => {
    const w = await enterWhileLoading(url)
    net.me!.resolve({ data: { username: 'p-admin', email: null, is_admin: false } })
    await waitForRoute('/settings')
    expect(location()).toBe('/app/settings')
    expect(w.find('[data-testid="settings-loading"]').exists()).toBe(false)
    expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(true)
    expectNoManagementDom(w)
    expect(settingsRequests()).toEqual([])
  })

  it('system/users, answer A → the view mounts only now and starts its GET', async () => {
    const w = await enterWhileLoading('/settings/system/users')
    net.me!.resolve({ data: { username: 'admin', email: 'admin@flowgate.local', is_admin: true } })
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/system/users')
    expect(w.find('[data-panel="users"]').exists()).toBe(true)
    expect(net.requests).toEqual(['GET /auth/me', 'GET /api/v1/system/users'])
  })

  it('system/general, answer A → the view mounts only now and starts its two GETs', async () => {
    const w = await enterWhileLoading('/settings/system/general')
    net.me!.resolve({ data: { username: 'admin', email: 'admin@flowgate.local', is_admin: true } })
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/system/general')
    expect(w.find('[data-panel="system-general"]').exists()).toBe(true)
    expect(net.requests).toEqual(['GET /auth/me', 'GET /api/v1/system/storage-root', 'GET /api/v1/system/settings'])
  })

  it('system/agents, answer A → the view mounts only now and starts its one GET', async () => {
    const w = await enterWhileLoading('/settings/system/agents')
    net.me!.resolve({ data: { username: 'admin', email: 'admin@flowgate.local', is_admin: true } })
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/system/agents')
    expect(w.find('[data-panel="system-agents"]').exists()).toBe(true)
    expect(net.requests).toEqual(['GET /auth/me', 'GET /api/v1/system/agents'])
  })

  it.each([
    ['/settings/system/projects', '[data-panel="system-projects"]', ['GET /api/v1/projects?status=all']],
    ['/settings/project/p1/paths', '[data-panel="project-paths"]', ['GET /api/v1/projects?status=all', 'GET /api/v1/projects/p1/storage-root']],
    ['/settings/project/p1/git', '[data-panel="project-git"]', ['GET /api/v1/projects?status=all', 'GET /api/v1/projects/p1/git/config']],
  ])('%s, answer A → the view mounts only now: the list GET, then the one GET of that project', async (url, panel, calls) => {
    const w = await enterWhileLoading(url)
    net.me!.resolve({ data: { username: 'admin', email: 'admin@flowgate.local', is_admin: true } })
    for (let i = 0; i < 50 && !w.find(panel).exists(); i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 10))
      await flushPromises()
    }
    expect(router.currentRoute.value.fullPath).toBe(url)
    expect(w.find(panel).exists()).toBe(true)
    expect(net.requests).toEqual(['GET /auth/me', ...calls])
  })

  it('/settings/project/p1 (no section), answer A → not a screen, so the overview; no request', async () => {
    const w = await enterWhileLoading('/settings/project/p1')
    net.me!.resolve({ data: { username: 'admin', email: 'admin@flowgate.local', is_admin: true } })
    await waitForRoute('/settings')
    expect(w.find('.settings-hub').exists()).toBe(true)
    expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(false)
    expect(settingsRequests()).toEqual([])
  })

  it('a failed /auth/me never opens an admin screen', async () => {
    const w = await enterWhileLoading('/settings/system/users')
    net.me!.reject(Object.assign(new Error('401'), { response: { status: 401 } }))
    await waitForRoute('/settings')
    expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(true)
    expectNoManagementDom(w)
    expect(settingsRequests()).toEqual([])
  })
})

describe('V04 폐지 URL — replaced by the overview, no old screen, Back does not return to it', () => {
  for (const [label, person] of [['A', ADMIN], ['P', NON_ADMINS[0][1]]] as const) {
    it.each(RETIRED_URLS)(`${label}: direct entry / refresh of %s`, async (url) => {
      setCurrentUser(person)
      const w = await enterDirectly(url)
      expect(router.currentRoute.value.fullPath).toBe('/settings')
      expect(location()).toBe('/app/settings') // no query / hash of the old screen survives
      expect(w.find('.settings-hub').exists()).toBe(true)
      expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(false)
      expect(w.find('[data-workspace], [data-tfa-scope], [data-testid="ws-search"]').exists()).toBe(false)
      expect(net.requests).toEqual([])
    })
  }

  it.each([['A', ADMIN], ['P', NON_ADMINS[0][1]]] as const)('%s: Back onto an old Workspace entry lands on the overview once, then moves on', async (_label, person) => {
    setCurrentUser(person)
    await router.push('/projects') // an earlier screen
    // The entry an earlier build's Workspace screen left in this tab's history (vue-router state included).
    const previous = window.history.state as { current: string; position: number }
    const retired = '/settings/workspaces/w1?tab=policy#members'
    window.history.pushState(
      { back: previous.current, current: retired, forward: null, position: previous.position + 1, replaced: false, scroll: null },
      '',
      `/app${retired}`,
    )
    await router.push('/settings')
    const w = mountHost()
    await flushPromises()
    const depth = window.history.length

    const landed = new Promise<void>((resolve) => {
      const off = router.afterEach(() => {
        off()
        resolve()
      })
    })
    router.back()
    await landed
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings')
    expect(location()).toBe('/app/settings')
    expect(window.history.length).toBe(depth) // replaced in place, nothing pushed

    router.back()
    await waitForRoute('/projects')
    expect(router.currentRoute.value.fullPath).toBe('/projects')
    expect(w.find('[data-workspace], [data-testid="ws-search"]').exists()).toBe(false)
    expect(net.requests).toEqual([])
  })

  it('other unknown /settings/* paths go to the overview; paths outside /settings keep their URL', async () => {
    setCurrentUser(ADMIN)
    await enterDirectly('/settings/whatever/deep?x=1')
    expect(router.currentRoute.value.fullPath).toBe('/settings')

    await router.push('/projects?tab=x')
    expect(router.currentRoute.value.fullPath).toBe('/projects?tab=x')
    expect(location()).toBe('/app/projects?tab=x')
    await router.push('/settingsx')
    expect(router.currentRoute.value.name).toBe('not-found')
  })
})

describe('decideSettingsAccess', () => {
  const ready = (isAdmin: boolean) => ({ status: 'ready' as const, user: { isAdmin } })
  const target = (path: string, registered = true, adminOnly = false) => ({ path, registered, adminOnly })

  it('follows D §1.2', () => {
    expect(decideSettingsAccess(target('/projects/x'), ready(false))).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings'), ready(false))).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings/workspaces/w1', false), ready(true))).toEqual({ kind: 'home', notice: null })
    expect(decideSettingsAccess(target('/settings/system/security', false), ready(false))).toEqual({ kind: 'home', notice: null })
    expect(decideSettingsAccess(target('/settings/system/users', true, true), { status: 'loading', user: null })).toEqual({ kind: 'pending' })
    expect(decideSettingsAccess(target('/settings/system/users', true, true), ready(false))).toEqual({ kind: 'home', notice: 'admin_only' })
    expect(decideSettingsAccess(target('/settings/system/users', true, true), ready(true))).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings/project/p1/git', false), ready(true))).toEqual({ kind: 'home', notice: null })
    expect(decideSettingsAccess(target('/settings/PROJECT', false), ready(false))).toEqual({ kind: 'home', notice: 'admin_only' })
    expect(decideSettingsAccess(target('/settings/system/users', true, true), { status: 'failed', user: null })).toEqual({ kind: 'home', notice: 'admin_only' })
    expect(decideSettingsAccess(target('/settings/system/users', true, true), { status: 'idle', user: null })).toEqual({ kind: 'home', notice: 'admin_only' })
    expect(decideSettingsAccess(target('/settings/me/account', false), ready(false))).toEqual({ kind: 'home', notice: null })
  })
})
