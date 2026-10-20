import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterView } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import router from '@/app/router/index'
import { setCurrentUser } from '@/core/auth/currentUser'
import { SESSION_CHECK_MS } from '@/features/settings/components/useLiveRequests'
import ProjectGitReadView from '@/features/settings/views/ProjectGitReadView.vue'
import ProjectPathsReadView from '@/features/settings/views/ProjectPathsReadView.vue'
import ProjectSettingsLayout from '@/features/settings/views/ProjectSettingsLayout.vue'
import SystemProjectsReadView from '@/features/settings/views/SystemProjectsReadView.vue'

// flowgate.v02.0076 T#5 (D §2.3 / §2.4 / §2.8, §5.2 V05 · V22 · V23 · V25 GUI part): the 프로젝트
// selection area, 기본 정보 / 저장 경로 / Git and 시스템 › 전체 프로젝트 on the real /app router
// (installAuthGuard + installSettingsAccessGuard + SettingsLayout) with the real views. Only the
// transport is replaced: @shared/api's request helpers answer from fixtures shaped like the server's
// GET /projects (list_projects + modules), GET /projects/{id}/storage-root (describe_storage_root)
// and GET /projects/{id}/git/config (get_config_view), can fail a call with a given status or hold
// it until released, and record every call with its query. The real routers / auth / RBAC are
// exercised by server/tests/test_project_read_0076.py (V24 / V25 server part).

const net = vi.hoisted(() => ({
  requests: [] as string[],
  projects: [] as unknown[],
  listBody: null as unknown,
  roots: {} as Record<string, unknown>,
  gits: {} as Record<string, unknown>,
  /** call → HTTP status to answer it with (every time, until removed). */
  fail: {} as Record<string, number>,
  /** calls to hold; each held call waits in `held` until released. */
  hold: new Set<string>(),
  held: [] as { call: string; release: () => void }[],
}))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const failure = (status: number) => Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: status === 403 ? { detail: 'Forbidden' } : { detail: `status ${status}` } },
  })

  function answer(call: string): { data: unknown } {
    const status = net.fail[call]
    if (status) throw failure(status)
    if (call === 'GET /api/v1/projects?status=all') return { data: net.listBody ?? { projects: net.projects } }
    let m = /^GET \/api\/v1\/projects\/([^/?]+)\/storage-root$/.exec(call)
    if (m) {
      const id = decodeURIComponent(m[1])
      if (!(id in net.roots)) throw Object.assign(new Error('404'), { response: { status: 404, data: { detail: 'Project not found' } } })
      return { data: net.roots[id] }
    }
    m = /^GET \/api\/v1\/projects\/([^/?]+)\/git\/config$/.exec(call)
    if (m) {
      const id = decodeURIComponent(m[1])
      return { data: net.gits[id] ?? { ok: true, configured: false, config: null } }
    }
    throw failure(500)
  }

  function handle(method: string, path: string, params?: Record<string, unknown>) {
    const query = method === 'GET' && params && Object.keys(params).length
      ? `?${new URLSearchParams(params as Record<string, string>).toString()}` : ''
    const call = `${method} ${path}${query}`
    net.requests.push(call)
    if (!net.hold.has(call)) return Promise.resolve().then(() => answer(call))
    return new Promise((resolve, reject) => {
      net.held.push({
        call,
        release: () => {
          try {
            resolve(answer(call))
          } catch (err) {
            reject(err)
          }
        },
      })
    })
  }

  return {
    ...actual,
    getRequest: vi.fn((path: string, params?: Record<string, unknown>) => handle('GET', path, params)),
    postRequest: vi.fn((path: string) => handle('POST', path)),
    patchRequest: vi.fn((path: string) => handle('PATCH', path)),
    putRequest: vi.fn((path: string) => handle('PUT', path)),
    deleteRequest: vi.fn((path: string) => handle('DELETE', path)),
  }
})

registerAppMessages(i18n)

const Host = defineComponent({ render: () => h(RouterView) })
const ADMIN = { username: 'admin', email: 'admin@flowgate.local', isAdmin: true }

const LIST = 'GET /api/v1/projects?status=all'
const ROOT = (id: string) => `GET /api/v1/projects/${id}/storage-root`
const GIT = (id: string) => `GET /api/v1/projects/${id}/git/config`

// p1 active (override shadowed by FLOWGATE_STORAGE_DIR, Git configured with a secret), p2 inactive
// (no override, Git not configured), p3 active (system Root, Git configured without secret / author).
// The database answers is_active as 1 / 0; __SYSTEM__ is the bookkeeping row every list drops.
function projectFixtures(): unknown[] {
  return [
    { project_id: '__SYSTEM__', project_name: 'System', description: null, is_active: 1, color: null, modules: [] },
    { project_id: 'p1', project_name: 'Alpha', description: 'First project', is_active: 1, color: '#ff0000', workspace_id: 'w1', modules: [{ name: 'v01', title: 'Version 1' }, { name: 'v02', title: 'v02' }] },
    { project_id: 'p2', project_name: 'Bravo (archived)', description: null, is_active: 0, color: null, modules: [] },
    { project_id: 'p3', project_name: 'Charlie', description: '', is_active: 1, color: null, modules: [{ name: 'main', title: 'main' }] },
  ]
}

const P1_ROOT = { project_id: 'p1', configured_value: '/srv/flowgate-system', project_override: '/data/p1-override', effective_value: '/env/flowgate', effective_source: 'env', env_override: true }
const P2_ROOT = { project_id: 'p2', configured_value: null, project_override: null, effective_value: '/opt/flowgate/storage', effective_source: 'default', env_override: false }
const P3_ROOT = { project_id: 'p3', configured_value: '/srv/flowgate-system', project_override: null, effective_value: '/srv/flowgate-system', effective_source: 'system_setting', env_override: false }
const SECRET_MASK = 'ghp_****9f2c'
const P1_GIT = {
  ok: true,
  configured: true,
  config: {
    project_id: 'p1', repo_url: 'https://git.example.com/team/alpha.git', provider: 'gitea', username: 'bot-user',
    secret_masked: SECRET_MASK, has_secret: true, base_branch: 'develop', default_finalize_action: 'push', enabled: true,
    translate_url: 'http://translate.local', author_name: 'Alpha Bot', author_email: 'alpha-bot@example.com',
    tr_scope_stage: 'warn', updated_at: '2026-10-19T01:02:03Z',
  },
}
const P3_GIT = {
  ok: true,
  configured: true,
  config: {
    project_id: 'p3', repo_url: 'git@git.example.com:team/charlie.git', provider: 'generic', username: null,
    secret_masked: null, has_secret: false, base_branch: 'main', default_finalize_action: 'wait', enabled: false,
    translate_url: null, author_name: null, author_email: null, tr_scope_stage: 'observe', updated_at: null,
  },
}

let wrapper: VueWrapper | null = null

async function open(url: string): Promise<VueWrapper> {
  window.history.replaceState({}, '', `/app${url}`)
  await router.replace(url)
  wrapper = mount(Host, { global: { plugins: [i18n, router], stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}

async function waitForPath(target: string): Promise<void> {
  for (let i = 0; i < 100 && router.currentRoute.value.path !== target; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10))
    await flushPromises()
  }
  await flushPromises()
}

/** Wait until the lazily loaded section view of the current route has mounted. */
async function waitFor(w: VueWrapper, selector: string): Promise<void> {
  for (let i = 0; i < 100 && !w.find(selector).exists(); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10))
    await flushPromises()
  }
}

async function release(call: string): Promise<void> {
  const index = net.held.findIndex((h) => h.call === call)
  expect(index).toBeGreaterThanOrEqual(0)
  const [held] = net.held.splice(index, 1)
  held.release()
  await flushPromises()
}

const text = (w: VueWrapper, testid: string) => w.get(`[data-testid="${testid}"]`).text().replace(/\s+/g, ' ').trim()
const options = (w: VueWrapper) => w.findAll('[data-testid="project-select"] option').map((o) => o.attributes('value'))
const subnav = (w: VueWrapper) => w.findAll('.subnav [data-subnav]').map((i) => i.attributes('data-subnav'))
const writes = () => net.requests.filter((r) => !r.startsWith('GET '))

/** A section panel offers no input and no change control: only 다시 불러오기 / 다시 시도. */
function expectReadOnly(w: VueWrapper, panel: string) {
  const el = w.get(`[data-panel="${panel}"]`)
  expect(el.findAll('input, select, textarea, form').length).toBe(0)
  for (const b of el.findAll('button')) expect(b.attributes('data-testid')).toMatch(/-(refresh|retry)$/)
}

beforeEach(async () => {
  i18n.global.locale.value = 'ko'
  sessionStorage.clear()
  window.__accessToken__ = 'token-projects-0076'
  net.projects = projectFixtures()
  net.listBody = null
  net.roots = { p1: P1_ROOT, p2: P2_ROOT, p3: P3_ROOT }
  net.gits = { p1: P1_GIT, p3: P3_GIT }
  net.fail = {}
  net.hold = new Set()
  net.held = []
  setCurrentUser(ADMIN)
  window.history.replaceState({}, '', '/app/')
  await router.replace('/')
  net.requests.length = 0
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  // D §2.3 / §2.4 / §2.8, T#5 지시 7 (V25 GUI part): GETs of the three existing contracts only — no
  // settings / paths GET, no /system/settings/storage-root, no legacy list, and no write at all.
  // flowgate.v02.0077: the administrator overview's 확인 필요 read is not a project request.
  for (const call of net.requests.filter((r) => r !== 'GET /api/v1/system/agents')) {
    expect(call).toMatch(/^GET \/api\/v1\/projects(\?status=all|\/[^/?]+\/(storage-root|git\/config))$/)
  }
  expect(writes()).toEqual([])
  setCurrentUser(null)
  delete window.__accessToken__
})

describe('V05 관리자 프로젝트 조회 — list, selection and the three read-only sections', () => {
  it('/settings/project: the list GET only, active projects by default, a selection hint and no detail GET', async () => {
    const w = await open('/settings/project')
    expect(net.requests).toEqual([LIST])
    expect(w.find('[data-panel="project-select"]').exists()).toBe(true)
    expect(options(w)).toEqual(['', 'p1', 'p3']) // the placeholder, then active rows; __SYSTEM__ dropped
    expect(w.get('[data-testid="project-select"] option[value=""]').attributes('disabled')).toBeDefined()
    expect(w.get('.fgs-check').text()).toContain('비활성 포함 (1)')
    expect(text(w, 'project-filter-hint')).toContain('서버 권한 필터가 아닙니다')
    expect(text(w, 'project-select-hint')).toBe('조회할 프로젝트를 선택하세요.')
    expect(subnav(w)).toEqual([]) // no section before a project is chosen
    expect(w.find('[data-panel="project-general"]').exists()).toBe(false)

    await w.get('[data-testid="project-include-inactive"]').setValue(true)
    expect(options(w)).toEqual(['', 'p1', 'p2', 'p3'])
    expect(w.get('[data-testid="project-select"] option[value="p2"]').text()).toBe('Bravo (archived) (비활성)') // the name; the ID stays in the URL
    expect(net.requests).toEqual([LIST]) // the filter is the screen's own rule: no new request
  })

  it('choosing a project opens its 기본 정보 from the list row; changing project keeps the section', async () => {
    const w = await open('/settings/project')
    await w.get('[data-testid="project-select"]').setValue('p1')
    await waitForPath('/settings/project/p1/general')
    await waitFor(w, '[data-panel="project-general"]')
    expect(text(w, 'project-name')).toBe('Alpha')
    expect(w.find('[data-panel="project-select"]').exists()).toBe(false) // the section alone, no second panel above it
    expect(net.requests).toEqual([LIST])

    await router.push('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    await w.get('[data-testid="project-select"]').setValue('p3')
    await waitForPath('/settings/project/p3/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    expect(text(w, 'root-effective')).toBe('/srv/flowgate-system')
    expect(net.requests).toEqual([LIST, ROOT('p1'), ROOT('p3')]) // the list is read once per entry
  })

  it('p1/general direct entry: 이름 / 설명 / 상태 / 모듈 of the list row (no internal ID), no request of its own', async () => {
    const w = await open('/settings/project/p1/general')
    await waitFor(w, '[data-panel="project-general"]')
    expect(w.find('[data-testid="project-id"]').exists()).toBe(false)
    expect(text(w, 'project-name')).toBe('Alpha')
    expect(text(w, 'project-description')).toBe('First project')
    expect(text(w, 'project-status')).toBe('활성')
    expect(w.findAll('[data-testid="project-module"]').map((m) => m.text())).toEqual(['Version 1 (v01)', 'v02'])
    expect(subnav(w)).toEqual(['general', 'paths', 'git'])
    expect(w.get('.subnav [data-subnav="general"]').attributes('aria-current')).toBe('page')
    expect(w.get('.subnav [data-subnav="paths"]').attributes('href')).toBe('/app/settings/project/p1/paths')
    expect(w.get('[data-panel="project-general"] .fgs-phead').text()).toContain('Alpha')
    expect(w.find('.crumbs').exists()).toBe(false)
    expect((w.get('[data-testid="project-select"]').element as HTMLSelectElement).value).toBe('p1')
    expectReadOnly(w, 'project-general')
    expect(w.text()).not.toContain('#ff0000') // only the five contract fields are shown
    expect(net.requests).toEqual([LIST])
  })

  it('p2 (inactive) direct entry: 비활성, no description / modules, and kept in the picker under the active filter', async () => {
    const w = await open('/settings/project/p2/general')
    await waitFor(w, '[data-panel="project-general"]')
    expect(text(w, 'project-status')).toBe('비활성')
    expect(text(w, 'project-description')).toBe('설명 없음')
    expect(text(w, 'project-modules')).toBe('모듈 없음')
    expect(options(w)).toEqual(['p1', 'p2', 'p3'])
    expect(net.requests).toEqual([LIST])
  })

  it('p1/paths: the p1 storage-root GET only', async () => {
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    expect(net.requests).toEqual([LIST, ROOT('p1')])
  })

  it('p1/git: the p1 git/config GET only', async () => {
    const w = await open('/settings/project/p1/git')
    await waitFor(w, '[data-testid="git-repo-url"]')
    expect(net.requests).toEqual([LIST, GIT('p1')])
  })

  it.each(['general', 'paths', 'git'])('p404/%s: 찾을 수 없음 and no detail GET for it', async (section) => {
    const w = await open(`/settings/project/p404/${section}`)
    expect(router.currentRoute.value.fullPath).toBe(`/settings/project/p404/${section}`)
    expect(text(w, 'project-not-found')).toContain('프로젝트를 찾을 수 없습니다')
    expect(text(w, 'project-not-found')).toContain('p404')
    expect(w.find('[data-panel^="project-"]:not([data-panel="project-select"])').exists()).toBe(false)
    expect(subnav(w)).toEqual([])
    expect(net.requests).toEqual([LIST])
  })

  it('/settings/project/p1 without a section is not a screen: the overview', async () => {
    await open('/settings/project/p1')
    await waitForPath('/settings')
    expect(router.currentRoute.value.fullPath).toBe('/settings')
    expect(net.requests).toEqual(['GET /api/v1/system/agents']) // no project request; the overview's 확인 필요 read only
  })

  it('system/projects: p1 · p2 · p3 read only with 활성 / 비활성, the p2 row opens p2/general; no Root / Git GET', async () => {
    const w = await open('/settings/system/projects')
    expect(net.requests).toEqual([LIST])
    expect(w.findAll('.subnav [data-subnav]').map((i) => i.attributes('data-subnav'))).toEqual(['general', 'agents', 'users', 'projects'])
    expect(w.get('.subnav [data-subnav="projects"]').attributes('aria-current')).toBe('page')
    const rows = w.findAll('[data-project-row]')
    expect(rows.map((r) => r.attributes('data-project-row'))).toEqual(['p1', 'p2', 'p3'])
    expect(rows.map((r) => Array.from(r.element.children).slice(0, 5).map((c) => c.textContent?.trim()))).toEqual([
      ['p1', 'Alpha', 'First project', '활성', 'v01, v02'],
      ['p2', 'Bravo (archived)', '—', '비활성', '—'],
      ['p3', 'Charlie', '—', '활성', 'main'],
    ])
    expect(rows[1].attributes('href')).toBe('/app/settings/project/p2/general')
    expect(w.get('[data-panel="system-projects"]').findAll('input, select, textarea, form').length).toBe(0)
    expect(w.findAll('[data-panel="system-projects"] button').map((b) => b.attributes('data-testid') ?? b.attributes('data-filter'))).toEqual(['projects-refresh', 'all', 'active', 'inactive'])

    await w.get('[data-filter="inactive"]').trigger('click')
    expect(w.findAll('[data-project-row]').map((r) => r.attributes('data-project-row'))).toEqual(['p2'])
    await w.get('[data-filter="active"]').trigger('click')
    expect(w.findAll('[data-project-row]').map((r) => r.attributes('data-project-row'))).toEqual(['p1', 'p3'])
    await w.get('[data-filter="all"]').trigger('click')
    expect(net.requests).toEqual([LIST])

    await w.get('[data-project-row="p2"]').trigger('click')
    await waitForPath('/settings/project/p2/general')
    await waitFor(w, '[data-panel="project-general"]')
    expect(text(w, 'project-name')).toBe('Bravo (archived)')
    expect(text(w, 'project-status')).toBe('비활성')
    expect(net.requests).toEqual([LIST, LIST]) // the selection area reads the same list; no Root / Git
  })

  it('A sees 프로젝트 and 전체 프로젝트 entries: hub cards, scope tabs and 시스템 subnav', async () => {
    const hub = await open('/settings')
    expect(hub.findAll('[data-scope-card]').map((c) => c.attributes('data-scope-card'))).toEqual(['me', 'project', 'system'])
    expect(hub.get('[data-scope-card="project"] a').attributes('href')).toBe('/app/settings/project')
    expect(hub.get('[data-scope-card="project"]').findAll('.tag').map((t) => t.text())).toEqual(['프로젝트 선택'])
    expect(hub.get('[data-scope-card="system"]').findAll('.tag').map((t) => t.text())).toEqual(['일반', '시스템 Agent', '사용자', '전체 프로젝트'])
    expect(net.requests).toEqual(['GET /api/v1/system/agents'])
  })
})

describe('V22 프로젝트 Root · Git 계약 — values as answered, null = 미설정, Secret only as registered or not', () => {
  it('p1 paths: the project override shadowed by FLOWGATE_STORAGE_DIR, each field kept apart', async () => {
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    expect(text(w, 'root-configured')).toBe('/srv/flowgate-system')
    expect(text(w, 'root-override')).toBe('/data/p1-override')
    expect(text(w, 'root-effective')).toBe('/env/flowgate')
    expect(text(w, 'root-source')).toBe('환경 변수')
    expect(text(w, 'root-env')).toBe('예 — FLOWGATE_STORAGE_DIR 적용')
    expect(text(w, 'root-override-shadowed')).toContain('FLOWGATE_STORAGE_DIR이 우선')
    expectReadOnly(w, 'project-paths')
  })

  it('p2 paths: null configured / override are 미설정, the default Root is the effective one', async () => {
    const w = await open('/settings/project/p2/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    expect(text(w, 'root-configured')).toBe('미설정')
    expect(text(w, 'root-override')).toBe('미설정')
    expect(text(w, 'root-effective')).toBe('/opt/flowgate/storage')
    expect(text(w, 'root-source')).toBe('기본값')
    expect(text(w, 'root-env')).toBe('아니요')
    expect(w.find('[data-testid="root-override-shadowed"]').exists()).toBe(false)
  })

  it('p3 paths: the system setting is the source', async () => {
    const w = await open('/settings/project/p3/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    expect(text(w, 'root-override')).toBe('미설정')
    expect(text(w, 'root-source')).toBe('시스템 설정')
  })

  it('p1 git (configured): the shown fields match the answer; the secret is only "등록됨", never its masked text', async () => {
    const w = await open('/settings/project/p1/git')
    await waitFor(w, '[data-testid="git-repo-url"]')
    expect(text(w, 'git-repo-url')).toBe('https://git.example.com/team/alpha.git')
    expect(text(w, 'git-provider')).toBe('gitea')
    expect(text(w, 'git-enabled')).toBe('사용')
    expect(text(w, 'git-base-branch')).toBe('develop')
    expect(text(w, 'git-finalize-action')).toBe('푸시 (push)')
    expect(text(w, 'git-author-name')).toBe('Alpha Bot')
    expect(text(w, 'git-author-email')).toBe('alpha-bot@example.com')
    expect(text(w, 'git-secret')).toBe('등록됨')
    expect(w.html()).not.toContain(SECRET_MASK)
    for (const hidden of ['bot-user', 'http://translate.local', 'warn', '2026-10-19']) expect(w.text()).not.toContain(hidden)
    expectReadOnly(w, 'project-git')
    const state = (w.findComponent(ProjectGitReadView).vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState
    expect(JSON.stringify(state.view)).not.toContain(SECRET_MASK) // not even kept in memory
  })

  it('p3 git (configured, no secret / author, disabled)', async () => {
    const w = await open('/settings/project/p3/git')
    await waitFor(w, '[data-testid="git-repo-url"]')
    expect(text(w, 'git-enabled')).toBe('사용 안 함')
    expect(text(w, 'git-finalize-action')).toBe('대기 (wait)')
    expect(text(w, 'git-author-name')).toBe('미설정 (FlowGate 기본값)')
    expect(text(w, 'git-author-email')).toBe('미설정 (FlowGate 기본값)')
    expect(text(w, 'git-secret')).toBe('등록 안 됨')
  })

  it('p2 git (configured=false, config=null): 미설정, not an error', async () => {
    const w = await open('/settings/project/p2/git')
    await waitFor(w, '[data-testid="git-unconfigured"]')
    expect(text(w, 'git-unconfigured')).toContain('Git이 설정되지 않았습니다')
    expect(w.find('[data-testid="git-error"]').exists()).toBe(false)
    expect(w.find('[data-testid="git-repo-url"]').exists()).toBe(false)
    expectReadOnly(w, 'project-git')
  })

  it.each([
    ['/settings/project/p1/general', '[data-panel="project-general"]'],
    ['/settings/project/p1/paths', '[data-testid="root-effective"]'],
    ['/settings/project/p1/git', '[data-testid="git-repo-url"]'],
  ])('%s offers no change control: only 다시 불러오기 / 다시 시도 buttons and the picker as the only input', async (url, ready) => {
    const w = await open(url)
    await waitFor(w, ready)
    const body = w.get('.settings-shell-view') // the picker sits in the navigation (시안 ⑩)
    for (const b of body.findAll('button')) expect(b.attributes('data-testid')).toMatch(/-(refresh|retry)$/)
    expect(body.findAll('input, select, textarea').map((e) => e.attributes('data-testid'))).toEqual(['project-select', 'project-include-inactive'])
    expect(body.findAll('form').length).toBe(0)
  })
})

describe('V23 조회 전환 · 실패 — late answers dropped, failures never shown as empty / 미설정 / success', () => {
  it('p1 Root answer held, switch to p2, then p1 arrives: p2 shows p2 only', async () => {
    net.hold.add(ROOT('p1'))
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="paths-loading"]')
    await w.get('[data-testid="project-include-inactive"]').setValue(true)
    await w.get('[data-testid="project-select"]').setValue('p2')
    await waitForPath('/settings/project/p2/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    expect(text(w, 'root-effective')).toBe('/opt/flowgate/storage')

    await release(ROOT('p1'))
    expect(text(w, 'root-effective')).toBe('/opt/flowgate/storage')
    expect(w.html()).not.toContain('/env/flowgate')
    expect(w.html()).not.toContain('/data/p1-override')
    expect(w.findAllComponents(ProjectPathsReadView).length).toBe(1)
    expect(net.requests).toEqual([LIST, ROOT('p1'), ROOT('p2')])
  })

  it('p1 Git answer held, switch to p3, then p1 arrives: the late p1 config never appears on p3', async () => {
    net.hold.add(GIT('p1'))
    const w = await open('/settings/project/p1/git')
    await waitFor(w, '[data-testid="git-loading"]')
    await w.get('[data-testid="project-select"]').setValue('p3')
    await waitForPath('/settings/project/p3/git')
    await waitFor(w, '[data-testid="git-repo-url"]')
    await release(GIT('p1'))
    expect(text(w, 'git-repo-url')).toBe('git@git.example.com:team/charlie.git')
    expect(w.html()).not.toContain('alpha.git')
    expect(w.html()).not.toContain('Alpha Bot')
  })

  it.each([
    [401, '로그인이 필요하거나 세션이 만료되어 프로젝트 목록을 불러오지 못했습니다.'],
    [403, '프로젝트 목록을 볼 권한이 없습니다.'],
    [500, '프로젝트 목록을 불러오지 못했습니다.'],
  ])('list GET %i: an error with retry — never the empty list, no detail GET; retry = the list GET only', async (status, title) => {
    net.fail[LIST] = status
    for (const url of ['/settings/project/p1/paths', '/settings/system/projects']) {
      net.requests.length = 0
      const w = await open(url)
      expect(w.get('[data-testid="projects-error"] .ui-error-state-title').text()).toBe(title)
      expect(w.find('[data-testid="projects-empty"]').exists()).toBe(false)
      expect(w.find('[data-testid="project-not-found"]').exists()).toBe(false)
      expect(w.find('[data-project-row]').exists()).toBe(false)
      expect(net.requests).toEqual([LIST])
      delete net.fail[LIST]
      await w.get('[data-testid="projects-retry"]').trigger('click')
      await flushPromises()
      expect(w.find('[data-testid="projects-error"]').exists()).toBe(false)
      expect(net.requests[1]).toBe(LIST)
      net.fail[LIST] = status
      w.unmount()
      wrapper = null
    }
  })

  it('only an actual projects=[] is the empty list', async () => {
    net.projects = [{ project_id: '__SYSTEM__', project_name: 'System', description: null, is_active: 1, modules: [] }]
    const w = await open('/settings/project')
    expect(text(w, 'projects-empty')).toBe('등록된 프로젝트가 없습니다.')
    expect(w.find('[data-testid="projects-error"]').exists()).toBe(false)
  })

  it('a list answer without the contract shape is an error, not an empty list', async () => {
    net.listBody = { items: [] }
    const w = await open('/settings/system/projects')
    expect(w.get('[data-testid="projects-error"]').text()).toContain('서버 응답에 필요한 값이 없습니다.')
    expect(w.find('[data-testid="projects-empty"]').exists()).toBe(false)
  })

  it.each([
    [401, '로그인이 필요하거나 세션이 만료되어 저장 경로를 불러오지 못했습니다.'],
    [403, '이 프로젝트의 저장 경로를 볼 권한이 없습니다.'],
    [500, '저장 경로를 불러오지 못했습니다.'],
  ])('storage-root GET %i: error, never 미설정; retry repeats this GET only (no other Root contract)', async (status, title) => {
    net.fail[ROOT('p1')] = status
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="paths-error"]')
    expect(w.get('[data-testid="paths-error"] .ui-error-state-title').text()).toBe(title)
    expect(w.find('[data-testid="root-configured"]').exists()).toBe(false)
    expect(w.text()).not.toContain('미설정')
    delete net.fail[ROOT('p1')]
    await w.get('[data-testid="paths-retry"]').trigger('click')
    await flushPromises()
    expect(text(w, 'root-effective')).toBe('/env/flowgate')
    expect(net.requests).toEqual([LIST, ROOT('p1'), ROOT('p1')])
  })

  it.each([
    [401, '로그인이 필요하거나 세션이 만료되어 Git 설정을 불러오지 못했습니다.'],
    [403, '이 프로젝트의 Git 설정을 볼 권한이 없습니다.'],
    [500, 'Git 설정을 불러오지 못했습니다.'],
  ])('git/config GET %i: error, never "Git이 설정되지 않았습니다"', async (status, title) => {
    net.fail[GIT('p1')] = status
    const w = await open('/settings/project/p1/git')
    await waitFor(w, '[data-testid="git-error"]')
    expect(w.get('[data-testid="git-error"] .ui-error-state-title').text()).toBe(title)
    expect(w.find('[data-testid="git-unconfigured"]').exists()).toBe(false)
    expect(w.text()).not.toContain('Git이 설정되지 않았습니다')
    expect(net.requests).toEqual([LIST, GIT('p1')])
  })

  it('an answer for another project or without the contract fields is an error', async () => {
    net.roots.p1 = { ...P1_ROOT, project_id: 'p3' }
    net.gits.p3 = { ok: true, configured: true, config: null }
    let w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="paths-error"]')
    expect(text(w, 'paths-error')).toContain('서버 응답에 필요한 값이 없습니다.')
    w.unmount()
    w = await open('/settings/project/p3/git')
    await waitFor(w, '[data-testid="git-error"]')
    expect(text(w, 'git-error')).toContain('서버 응답에 필요한 값이 없습니다.')
    expect(w.find('[data-testid="git-unconfigured"]').exists()).toBe(false)
  })

  it('reload where the selected project is gone: selection and section cleared, with a notice', async () => {
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    net.projects = projectFixtures().filter((p) => (p as { project_id: string }).project_id !== 'p1')
    await w.get('[data-testid="projects-refresh"]').trigger('click')
    await waitForPath('/settings/project')
    expect(router.currentRoute.value.fullPath).toBe('/settings/project')
    expect(text(w, 'project-removed')).toContain('p1')
    expect(w.find('[data-panel="project-paths"]').exists()).toBe(false)
    expect(w.html()).not.toContain('/env/flowgate')
    expect(options(w)).toEqual(['', 'p3'])
    expect(net.requests).toEqual([LIST, ROOT('p1'), LIST])
  })

  it('reload keeps a project that is still listed (no notice) and a not-found URL stays not found', async () => {
    let w = await open('/settings/project/p1/general')
    await waitFor(w, '[data-panel="project-general"]')
    await w.get('[data-testid="projects-refresh"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/project/p1/general')
    expect(w.find('[data-testid="project-removed"]').exists()).toBe(false)
    expect(text(w, 'project-name')).toBe('Alpha')
    w.unmount()
    w = await open('/settings/project/p404/general')
    await w.get('[data-testid="projects-refresh"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/project/p404/general')
    expect(w.find('[data-testid="project-not-found"]').exists()).toBe(true)
  })

  it('a failed reload drops the list and the section view (no stale detail under an error)', async () => {
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    net.fail[LIST] = 403
    await w.get('[data-testid="projects-refresh"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="projects-error"]').exists()).toBe(true)
    expect(w.find('[data-panel="project-paths"]').exists()).toBe(false)
    expect(w.html()).not.toContain('/env/flowgate')
  })

  it('sign-out while a Root answer is out: the overview, no management DOM, the late answer dropped, no more GETs', async () => {
    net.hold.add(ROOT('p1'))
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="paths-loading"]')
    const view = w.findComponent(ProjectPathsReadView)
    setCurrentUser(null)
    await waitForPath('/settings')
    delete window.__accessToken__
    await release(ROOT('p1'))
    expect(w.find('[data-panel^="project-"]').exists()).toBe(false)
    expect(w.html()).not.toContain('/env/flowgate')
    expect((view.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState.root).toBeNull()
    expect(net.requests).toEqual([LIST, ROOT('p1')])
  })

  it('administrator role lost on a shown section: data removed, overview with notice, no more GETs', async () => {
    const w = await open('/settings/project/p1/git')
    await waitFor(w, '[data-testid="git-repo-url"]')
    setCurrentUser({ ...ADMIN, isAdmin: false })
    await waitForPath('/settings')
    expect(w.find('[data-testid="settings-access-notice"]').exists()).toBe(true)
    expect(w.find('[data-panel^="project-"]').exists()).toBe(false)
    expect(w.html()).not.toContain('alpha.git')
    expect(net.requests).toEqual([LIST, GIT('p1')])
  })

  it('administrator role lost while the list is out: the late list is dropped, no section GET follows', async () => {
    net.hold.add(LIST)
    const w = await open('/settings/project/p1/paths')
    setCurrentUser({ ...ADMIN, isAdmin: false })
    await waitForPath('/settings')
    await release(LIST)
    await flushPromises()
    expect(w.find('[data-panel^="project-"]').exists()).toBe(false)
    expect(net.requests).toEqual([LIST])
  })

  it('token lost while a Git answer is out (screen still shown): the answer is dropped and the whole project area with it', async () => {
    net.hold.add(GIT('p1'))
    const w = await open('/settings/project/p1/git')
    await waitFor(w, '[data-testid="git-loading"]')
    const layout = w.findComponent(ProjectSettingsLayout)
    delete window.__accessToken__
    await release(GIT('p1'))
    expect(w.find('[data-testid="git-repo-url"]').exists()).toBe(false)
    expect(w.html()).not.toContain('alpha.git')
    // The parent list was answered long before; the Git view's finding ends it as well.
    expectProjectAreaCleared(w, layout)
    await clickAll(w, ['projects-refresh'])
    expectProjectAreaCleared(w, layout)
    expect(net.requests).toEqual([LIST, GIT('p1')])
  })

  it('token lost on a loaded p1/paths screen, nothing out: 다시 불러오기 sends nothing and clears list, picker and section', async () => {
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="root-effective"]')
    const layout = w.findComponent(ProjectSettingsLayout)
    expect(options(w)).toEqual(['p1', 'p3'])
    delete window.__accessToken__
    await clickAll(w, ['projects-refresh'])
    expectProjectAreaCleared(w, layout)
    expect(w.html()).not.toContain('/env/flowgate')
    await clickAll(w, ['projects-refresh', 'projects-retry', 'paths-refresh', 'paths-retry'])
    expectProjectAreaCleared(w, layout)
    expect(net.requests).toEqual([LIST, ROOT('p1')])
  })

  it('token lost on a loaded p1/git screen: the section 다시 불러오기 sends nothing and ends the parent list too', async () => {
    const w = await open('/settings/project/p1/git')
    await waitFor(w, '[data-testid="git-repo-url"]')
    const layout = w.findComponent(ProjectSettingsLayout)
    sessionStorage.clear()
    delete window.__accessToken__
    await clickAll(w, ['git-refresh'])
    expectProjectAreaCleared(w, layout)
    expect(w.html()).not.toContain('alpha.git')
    await clickAll(w, ['projects-refresh', 'git-refresh', 'git-retry'])
    expect(net.requests).toEqual([LIST, GIT('p1')])
  })

  it('token lost on a failed Root: 다시 시도 sends nothing and the list goes with the error', async () => {
    net.fail[ROOT('p1')] = 500
    const w = await open('/settings/project/p1/paths')
    await waitFor(w, '[data-testid="paths-error"]')
    const layout = w.findComponent(ProjectSettingsLayout)
    delete net.fail[ROOT('p1')]
    delete window.__accessToken__
    await clickAll(w, ['paths-retry'])
    expectProjectAreaCleared(w, layout)
    expect(w.find('[data-testid="paths-error"]').exists()).toBe(false)
    expect(net.requests).toEqual([LIST, ROOT('p1')])
  })

  it('token lost on a loaded p1/general screen (no request at all): the periodic check clears the area; reloading sends nothing', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    try {
      const w = await open('/settings/project/p1/general')
      await waitFor(w, '[data-panel="project-general"]')
      const layout = w.findComponent(ProjectSettingsLayout)
      expect(w.get('[data-panel="project-general"]').text()).toContain('Alpha')
      delete window.__accessToken__
      vi.advanceTimersByTime(SESSION_CHECK_MS - 1)
      await flushPromises()
      expect(w.find('[data-panel="project-general"]').exists()).toBe(true) // not checked yet
      vi.advanceTimersByTime(1)
      await flushPromises()
      expectProjectAreaCleared(w, layout)
      await clickAll(w, ['projects-refresh'])
      vi.advanceTimersByTime(SESSION_CHECK_MS * 3)
      await flushPromises()
      expectProjectAreaCleared(w, layout)
      expect(net.requests).toEqual([LIST])
    } finally {
      vi.useRealTimers()
    }
  })

  it('token lost on a loaded system/projects table: the periodic check drops the rows; 다시 불러오기 sends nothing', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    try {
      const w = await open('/settings/system/projects')
      await waitFor(w, '[data-project-row="p1"]')
      const view = w.findComponent(SystemProjectsReadView)
      delete window.__accessToken__
      vi.advanceTimersByTime(SESSION_CHECK_MS)
      await flushPromises()
      expect(w.find('[data-project-row]').exists()).toBe(false)
      expect(w.find('[data-testid="projects-session-lost"]').exists()).toBe(true)
      expect(w.html()).not.toContain('Alpha')
      expect(setupState(view).projects).toBeNull()
      await clickAll(w, ['projects-refresh', 'projects-retry'])
      expect(w.find('[data-project-row]').exists()).toBe(false)
      expect(net.requests).toEqual([LIST])
    } finally {
      vi.useRealTimers()
    }
  })

  it('a token still there keeps a loaded screen across the periodic check (no request of its own)', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    try {
      const w = await open('/settings/project/p1/paths')
      await waitFor(w, '[data-testid="root-effective"]')
      vi.advanceTimersByTime(SESSION_CHECK_MS * 5)
      await flushPromises()
      expect(text(w, 'root-effective')).toBe('/env/flowgate')
      expect(options(w)).toEqual(['p1', 'p3'])
      expect(w.find('[data-testid="projects-session-lost"]').exists()).toBe(false)
      expect(net.requests).toEqual([LIST, ROOT('p1')])
    } finally {
      vi.useRealTimers()
    }
  })
})

function setupState(c: { vm: unknown }): Record<string, unknown> {
  return (c.vm as { $: { setupState: Record<string, unknown> } }).$.setupState
}

/** Click every listed button that is on screen now (a missing one is not an error: it is gone). */
async function clickAll(w: VueWrapper, testids: string[]): Promise<void> {
  for (const id of testids) {
    const b = w.find(`[data-testid="${id}"]`)
    if (b.exists()) await b.trigger('click')
    await flushPromises()
  }
}

/** The selection area after a lost session: no list, picker, section, project name or subnav; a notice. */
function expectProjectAreaCleared(w: VueWrapper, layout: { vm: unknown }): void {
  expect(setupState(layout).projects).toBeNull()
  expect(w.find('[data-testid="projects-session-lost"]').exists()).toBe(true)
  expect(w.find('[data-testid="project-select"]').exists()).toBe(false)
  expect(w.find('[data-card="project-picker"]').exists()).toBe(false)
  expect(w.findAll('[data-panel^="project-"]').map((p) => p.attributes('data-panel'))).toEqual(['project-select'])
  expect(subnav(w)).toEqual([])
  expect(w.html()).not.toContain('Alpha')
  expect(w.html()).not.toContain('Charlie')
}
