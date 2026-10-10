/**
 * flowgate.v02.0048 T#4 — the v0.1 main end of the v0.2 Inspector's ↗ 열기 (D#1 §9.3 / §9.4 / §9.7).
 *
 * A tab opened with 'noopener' at `/main/?doc=<doc_id>` starts with an empty sessionStorage. This
 * spec boots the real `src/main/main.ts` (real router guard, real `@shared/api` token rotation —
 * only the HTTP calls are faked) under each login state × document state:
 *
 * - 로그인 유지 on (refresh token in localStorage): the boot rotates once through the shared lock
 *   path, then the document opens (있음), or the existing "could not open" / permission toast shows
 *   (없음 404 / 권한 없음 403). `doc` leaves the URL in every case.
 * - 로그인 유지 off (refresh token in the opener's sessionStorage only, so nothing here): no
 *   rotation, the guard sends the tab to /login?next=/main/?doc=<doc_id>, the login page returns to
 *   that same URL, and the boot after login opens the document / shows the same toasts.
 *
 * The App root is a stub that makes the one App.vue call (`useDocDeepLink().openDocFromQuery()` on
 * mount); the dashboard view is a stub. Everything between — the project store, the explorer
 * group-tree reveal (`useDashboardNavigation`), the tabs store — is real.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const DOC = 'flowgate.v02.0048.0011-TR'
const GROUP = 'flowgate.v02.0048'
const HOME_PROJECT = 'otherproj'
const DOC_PROJECT = 'flowgate'
const REFRESH = 'refresh-token-remembered'

const http = vi.hoisted(() => ({
  getRequest: vi.fn(),
  startTokenAutoRefresh: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest: http.getRequest,
  startTokenAutoRefresh: http.startTokenAutoRefresh,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: http.showToast, toasts: { value: [] }, dismissToast: vi.fn() }),
}))

vi.mock('@main/views/DashboardView.vue', () => ({ default: { name: 'DashboardViewStub', render: () => null } }))
vi.mock('@main/views/RequirementCreateView.vue', () => ({ default: { name: 'RequirementCreateStub', render: () => null } }))

// App.vue's deep-link call, without the rest of the app root (dialogs, AI runs, Git hosts).
vi.mock('@main/App.vue', async () => {
  const { defineComponent, h, onMounted } = await import('vue')
  const { RouterView } = await import('vue-router')
  const { useDocDeepLink } = await import('@main/composables/useDocDeepLink')
  return {
    default: defineComponent({
      name: 'AppStub',
      setup() {
        const { openDocFromQuery } = useDocDeepLink()
        onMounted(() => {
          void openDocFromQuery()
        })
        return () => h(RouterView)
      },
    }),
  }
})

type DocState = 'exists' | 'missing' | 'forbidden'

const jwt = (sub: string) =>
  ['h', btoa(JSON.stringify({ sub, exp: Math.floor(Date.now() / 1000) + 3600 })), 's'].join('.')
const ROTATED_ACCESS = jwt('u-rotated')
const ROTATED_REFRESH = 'refresh-token-rotated'
const LOGIN_ACCESS = jwt('u-login')

function httpError(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail: status === 404 ? `Document not found: ${DOC}` : 'Forbidden' } },
  })
}

const TREE_NODES = [
  { id: `project:${DOC_PROJECT}`, parent_id: null, node_type: 'project', label: DOC_PROJECT },
  { id: GROUP, parent_id: `project:${DOC_PROJECT}`, node_type: 'group', label: '0048' },
  {
    id: DOC,
    parent_id: GROUP,
    node_type: 'document',
    label: `${DOC} — 작업레포트`,
    md_path: 'documents/0011-TR_document.md',
    has_md: true,
    type_code: 'TR',
  },
]

/** The server, by URL. Every document read is recorded so a test can assert none happened. */
function serve(doc: DocState) {
  http.getRequest.mockImplementation(async (path: string) => {
    if (path === '/auth/me') return { data: { user_id: 'u1' } }
    if (path === `/api/v1/documents/${encodeURIComponent(DOC)}`) {
      if (doc === 'missing') throw httpError(404)
      if (doc === 'forbidden') throw httpError(403)
      return { data: { doc_id: DOC, project_id: DOC_PROJECT, group_id: GROUP, type_code: 'TR' } }
    }
    if (path.startsWith(`/api/v1/projects/${DOC_PROJECT}/groups/tree?`)) return { data: { data: { nodes: TREE_NODES } } }
    if (path.startsWith('/api/v1/projects/')) return { data: { data: { nodes: [] } } }
    throw new Error(`unexpected GET ${path}`)
  })
}

const documentReads = () =>
  http.getRequest.mock.calls.filter(([path]) => String(path).startsWith('/api/v1/documents/'))

// --- Location: jsdom cannot navigate, so `href =` / `replace()` are recorded instead ----------------

const realLocation = window.location
let navigations: string[] = []

function installLocation() {
  navigations = []
  const fake = {
    get href() { return realLocation.href },
    set href(value: string) { navigations.push(value) },
    get origin() { return realLocation.origin },
    get protocol() { return realLocation.protocol },
    get host() { return realLocation.host },
    get hostname() { return realLocation.hostname },
    get port() { return realLocation.port },
    get pathname() { return realLocation.pathname },
    get search() { return realLocation.search },
    get hash() { return realLocation.hash },
    assign: (value: string) => { navigations.push(value) },
    replace: (value: string) => { navigations.push(value) },
    reload: () => {},
  }
  delete (window as unknown as { location?: unknown }).location
  ;(window as unknown as { location: unknown }).location = fake
}

function restoreLocation() {
  delete (window as unknown as { location?: unknown }).location
  ;(window as unknown as { location: Location }).location = realLocation
}

// --- Boot ---------------------------------------------------------------------------------------------

let refreshPost: ReturnType<typeof vi.fn>
let lockNames: string[]

/** A fresh `/main/?doc=<doc_id>` tab: boots main.ts and returns the app's stores. */
async function bootTab(url = `/main/?doc=${encodeURIComponent(DOC)}`) {
  vi.resetModules()
  window.history.replaceState(null, '', url)
  document.body.innerHTML = '<div id="app"></div>'
  const axios = (await import('axios')).default
  refreshPost = vi.fn(async (_url: string, body: { refresh_token: string }) => {
    if (body.refresh_token !== REFRESH) throw httpError(401)
    return { data: { access_token: ROTATED_ACCESS, refresh_token: ROTATED_REFRESH } }
  })
  vi.spyOn(axios, 'post').mockImplementation(refreshPost as never)
  await import('@main/main')
  const { useProjectStore } = await import('@main/stores/project')
  const { useTabsStore } = await import('@main/stores/tabs')
  return { projectStore: () => useProjectStore(), tabsStore: () => useTabsStore() }
}

const mounted = () => document.querySelector('#app')?.hasAttribute('data-v-app') === true

/** Waits until the deep link finished: `doc` left the URL. */
async function settled() {
  await vi.waitFor(() => expect(realLocation.search).not.toContain('doc='), { timeout: 3000 })
}

/** 로그인 유지 off: what the login page leaves in this tab after a session-only login. */
function sessionOnlyLogin() {
  window.__accessToken__ = LOGIN_ACCESS
  sessionStorage.setItem('fg_access_token', LOGIN_ACCESS)
  sessionStorage.setItem('fg_refresh_token', 'refresh-token-session-only')
}

beforeEach(() => {
  installLocation()
  localStorage.clear()
  sessionStorage.clear()
  delete window.__accessToken__
  http.getRequest.mockReset()
  http.startTokenAutoRefresh.mockReset()
  http.showToast.mockReset()
  lockNames = []
  // The cross-tab Web Lock the rotation must run under (jsdom has none of its own).
  vi.stubGlobal('navigator', {
    ...navigator,
    locks: {
      request: (name: string, cb: () => Promise<unknown>) => {
        lockNames.push(name)
        return cb()
      },
    },
  })
  localStorage.setItem('fg_current_project_id', HOME_PROJECT)
})

afterEach(() => {
  restoreLocation()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
  window.history.replaceState(null, '', '/')
})

function expectOpened(stores: Awaited<ReturnType<typeof bootTab>>) {
  expect(stores.projectStore().currentProjectId).toBe(DOC_PROJECT)
  expect(stores.tabsStore().activeTabId).toBe(DOC)
  expect(stores.tabsStore().tabs.find((tab) => tab.id === DOC)).toMatchObject({ id: DOC, type: 'md', typeCode: 'TR' })
  expect(http.showToast).not.toHaveBeenCalled()
}

function expectNotOpened(stores: Awaited<ReturnType<typeof bootTab>>, toast: string) {
  expect(stores.tabsStore().tabs.find((tab) => tab.id === DOC)).toBeUndefined()
  expect(stores.projectStore().currentProjectId).toBe(HOME_PROJECT)
  expect(http.showToast).toHaveBeenCalledTimes(1)
  expect(http.showToast).toHaveBeenCalledWith(toast, 'warning')
}

const NAVIGATION_FAILED = '대상을 열 수 없습니다.'
const FORBIDDEN = '접근 권한이 없습니다.'

describe('로그인 유지 on — the boot rotates the localStorage refresh token once, then the deep link runs', () => {
  beforeEach(() => {
    localStorage.setItem('fg_refresh_token', REFRESH)
  })

  function expectRotatedOnce() {
    expect(refreshPost).toHaveBeenCalledTimes(1)
    expect(String(refreshPost.mock.calls[0][0])).toMatch(/\/auth\/refresh$/)
    expect(refreshPost.mock.calls[0][1]).toEqual({ refresh_token: REFRESH })
    expect(lockNames).toEqual(['fg_token_refresh'])
    expect(window.__accessToken__).toBe(ROTATED_ACCESS)
    expect(sessionStorage.getItem('fg_access_token')).toBe(ROTATED_ACCESS)
    expect(localStorage.getItem('fg_refresh_token')).toBe(ROTATED_REFRESH)
    expect(http.startTokenAutoRefresh).toHaveBeenCalledTimes(1)
    expect(navigations).toEqual([])
  }

  it('문서 있음: switches to the document project, reveals it and opens its tab; doc leaves the URL', async () => {
    serve('exists')
    const stores = await bootTab()
    await settled()
    expectRotatedOnce()
    expectOpened(stores)
    expect(realLocation.pathname).toBe('/main/')
    expect(realLocation.search).toBe('')
  })

  it('문서 없음 (404): the existing "could not open" toast, no tab, project unchanged; doc leaves the URL', async () => {
    serve('missing')
    const stores = await bootTab()
    await settled()
    expectRotatedOnce()
    expectNotOpened(stores, NAVIGATION_FAILED)
  })

  it('권한 없음 (403): the existing permission toast, no tab, project unchanged; doc leaves the URL', async () => {
    serve('forbidden')
    const stores = await bootTab()
    await settled()
    expectRotatedOnce()
    expectNotOpened(stores, FORBIDDEN)
  })

  it('a refresh token the server refuses: no session, so /login?next=<the deep link>; nothing is read', async () => {
    localStorage.setItem('fg_refresh_token', 'refresh-token-revoked')
    serve('exists')
    await bootTab()
    await vi.waitFor(() => expect(navigations).toHaveLength(1))
    expect(refreshPost).toHaveBeenCalledTimes(1)
    expect(navigations[0]).toBe(`/login?next=${encodeURIComponent(`/main/?doc=${DOC}`)}`)
    expect(documentReads()).toEqual([])
  })
})

describe('로그인 유지 off — /login?next= and back to the same document', () => {
  it('a new tab has nothing to rotate: no refresh call, the guard sends it to /login?next=/main/?doc=<doc_id>', async () => {
    serve('exists')
    await bootTab()
    await vi.waitFor(() => expect(navigations).toHaveLength(1))
    expect(refreshPost).not.toHaveBeenCalled()
    expect(lockNames).toEqual([])
    expect(navigations[0]).toBe(`/login?next=${encodeURIComponent(`/main/?doc=${DOC}`)}`)
    expect(documentReads()).toEqual([])
    // The tab is leaving for /login: the app is not mounted behind it.
    await new Promise((done) => setTimeout(done, 20))
    expect(mounted()).toBe(false)
  })

  it('a session-only refresh token (sessionStorage) is not rotated at boot either', async () => {
    sessionStorage.setItem('fg_refresh_token', 'refresh-token-session-only')
    serve('exists')
    await bootTab()
    await vi.waitFor(() => expect(navigations).toHaveLength(1))
    expect(refreshPost).not.toHaveBeenCalled()
    expect(navigations[0]).toBe(`/login?next=${encodeURIComponent(`/main/?doc=${DOC}`)}`)
  })

  it('the login page returns to that same /main/?doc=<doc_id> URL', async () => {
    window.history.replaceState(null, '', `/login?next=${encodeURIComponent(`/main/?doc=${DOC}`)}`)
    vi.resetModules()
    const { authRedirect } = await import('@login/stores/auth')
    authRedirect.toDashboard()
    expect(navigations).toEqual([`/main/?doc=${DOC}`])
  })

  it.each<[DocState, string | null]>([
    ['exists', null],
    ['missing', NAVIGATION_FAILED],
    ['forbidden', FORBIDDEN],
  ])('after the login (문서 %s): the deep link opens the document or shows the same toast', async (doc, toast) => {
    sessionOnlyLogin()
    serve(doc)
    const stores = await bootTab()
    await settled()
    expect(refreshPost).not.toHaveBeenCalled()
    expect(navigations).toEqual([])
    if (toast === null) expectOpened(stores)
    else expectNotOpened(stores, toast)
    expect(realLocation.search).toBe('')
  })
})

describe('the /main guard: /login?next=<the /main URL asked for>, allow-listed values only', () => {
  it.each<[string, string]>([
    [`/main/?doc=${DOC}`, `/login?next=${encodeURIComponent(`/main/?doc=${DOC}`)}`],
    ['/main/requirements/create?x=1', `/login?next=${encodeURIComponent('/main/requirements/create?x=1')}`],
    ['/main', `/login?next=${encodeURIComponent('/main')}`],
  ])('%s → %s', async (target, expected) => {
    const { loginRedirectFor } = await import('@main/router')
    expect(loginRedirectFor(target)).toBe(expected)
  })

  it.each([
    '//evil.example.com/main/?doc=x',
    '/\\evil.example.com/main',
    'https://evil.example.com/main/?doc=x',
    '/elsewhere?doc=x',
    '/main/../admin',
    '',
  ])('a value outside the allow-list (%s) is dropped: plain /login', async (target) => {
    const { loginRedirectFor } = await import('@main/router')
    expect(loginRedirectFor(target)).toBe('/login')
  })

  it('an in-app navigation without a session also keeps the /main base in next', async () => {
    sessionOnlyLogin()
    serve('exists')
    await bootTab('/main/')
    await vi.waitFor(() => expect(mounted()).toBe(true))
    const router = (await import('@main/router')).default
    delete window.__accessToken__
    sessionStorage.removeItem('fg_access_token')
    await router.push({ path: '/requirements/create', query: { doc: DOC } })
    expect(navigations).toEqual([`/login?next=${encodeURIComponent(`/main/requirements/create?doc=${DOC}`)}`])
  })
})

describe('the deep link keeps tokens out of URLs and runs once', () => {
  it('neither the login redirect nor the document read carries a token', async () => {
    localStorage.setItem('fg_refresh_token', REFRESH)
    serve('exists')
    await bootTab()
    await settled()
    const urls = [...navigations, ...http.getRequest.mock.calls.map(([path]) => String(path)), realLocation.href]
    for (const url of urls) {
      for (const secret of [REFRESH, ROTATED_ACCESS, ROTATED_REFRESH, 'token']) expect(url).not.toContain(secret)
    }
    expect(documentReads()).toHaveLength(1)
  })

  it('without ?doc= the boot reads no document and changes no project', async () => {
    sessionOnlyLogin()
    serve('exists')
    const stores = await bootTab('/main/')
    await vi.waitFor(() => expect(mounted()).toBe(true))
    await new Promise((done) => setTimeout(done, 20))
    expect(documentReads()).toEqual([])
    expect(stores.projectStore().currentProjectId).toBe(HOME_PROJECT)
    expect(http.showToast).not.toHaveBeenCalled()
  })

  it('an empty ?doc= is dropped without a read or a toast; other query values stay', async () => {
    sessionOnlyLogin()
    serve('exists')
    await bootTab('/main/?doc=&keep=1')
    await settled()
    expect(documentReads()).toEqual([])
    expect(http.showToast).not.toHaveBeenCalled()
    expect(realLocation.search).toBe('?keep=1')
  })

  it('App.vue makes the deep-link call once on mount', () => {
    const app = readFileSync(resolve(__dirname, '../../src/main/App.vue'), 'utf8')
    expect(app).toMatch(/import \{ useDocDeepLink \} from '\.\/composables\/useDocDeepLink'/)
    expect(app.match(/openDocFromQuery\(\)/g)).toHaveLength(1)
    expect(app).toMatch(/onMounted\(\(\) => \{[\s\S]*void openDocFromQuery\(\)[\s\S]*\}\)/)
  })
})
