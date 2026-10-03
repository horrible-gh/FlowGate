import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { FlowRef, Project } from '@/features/project'

// flowgate.v02.0024 T0005 — Designer File Explorer end to end below the transport: only
// `@shared/api` is replaced, so features/project's api + store, the adapter and every
// component run for real. The fake server answers `/changes` according to the `base` query,
// the way the server-side `base` parameter does.
const getRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { DesignerFileExplorer } from '@/features/designer'
import { useProjectStore } from '@/features/project'

registerAppMessages(i18n)

const PID = 'demo'
const FLOW_A: FlowRef = { groupId: 'demo.v02.0001', name: '로그인 오류 수정', branch: 'flow-0001' }
const FLOW_B: FlowRef = { groupId: 'demo.v02.0002', name: '검색 개선', branch: 'flow-0002' }

interface RawNode { id: string; parent_id: string | null; type: 'folder' | 'file'; name: string; label: string; path: string; permissions: string[] }

function node(path: string, type: 'folder' | 'file' = 'file'): RawNode {
  const segs = path.split('/')
  const parent = segs.slice(0, -1).join('/')
  return { id: `n:${path}`, parent_id: parent ? `n:${parent}` : null, type, name: segs[segs.length - 1], label: segs[segs.length - 1], path, permissions: ['read', 'write'] }
}

const TREE: RawNode[] = [
  node('src', 'folder'),
  node('src/app.ts'),
  node('src/util.ts'),
  node('src/new.ts'),
  node('docs', 'folder'),
  node('docs/guide.md'),
  node('README.md'),
]

type Outcome<T> = T | 'fail'

interface FakeServer {
  // The stored Project Base (git config base_branch); 'fail' = git/status errors.
  base: Outcome<string | null>
  // 'legacy' = a server that predates base_branch_configured.
  statusShape?: 'current' | 'legacy'
  slots: { group_id: string; writable?: boolean }[]
  tree: Outcome<RawNode[]>
  // What `/changes` answers for a requested base (undefined = no base query sent).
  changes: (base: string | undefined) => Outcome<{ base_branch: string | null; changes: { path: string; status: string }[] }>
  active: Outcome<boolean>
  // Per-group gate so a test can hold one Flow's tree response back.
  treeGate?: Record<string, Promise<void>>
}

let server: FakeServer

function defaultServer(): FakeServer {
  return {
    base: 'v0.2',
    slots: [{ group_id: FLOW_A.groupId, writable: true }, { group_id: FLOW_B.groupId, writable: true }],
    tree: TREE,
    changes: (base) => ({
      base_branch: base ?? 'group-work-base',
      changes: [
        { path: 'src/app.ts', status: 'M' },
        { path: 'src/new.ts', status: '?' },
        { path: 'docs/old.md', status: 'D' },
      ],
    }),
    active: false,
  }
}

const fail = () => Promise.reject(new Error('boom'))

function calls(fragment: string) {
  return getRequest.mock.calls.filter(([url]) => String(url).includes(fragment))
}

beforeEach(() => {
  server = defaultServer()
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (url === `/api/v1/projects/${PID}/git/status`) {
      if (server.base === 'fail') return fail()
      // Same shape as server project_git_status: an unset/blank stored Base is reported as
      // its legacy "main" fallback, flagged by base_branch_configured=false.
      const stored = (server.base ?? '').trim()
      const status: Record<string, unknown> = { enabled: true, base_branch: stored || 'main', slots: server.slots }
      if (server.statusShape !== 'legacy') status.base_branch_configured = stored !== ''
      return { data: { ok: true, status } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') {
      await server.treeGate?.[decodeURIComponent(group[1])]
      if (server.tree === 'fail') return fail()
      const nodes = decodeURIComponent(group[1]) === FLOW_B.groupId
        ? [node('lib', 'folder'), node('lib/search.ts')]
        : server.tree
      return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes } } }
    }
    if (group && group[2] === 'changes') {
      const answer = server.changes(params.base as string | undefined)
      if (answer === 'fail') return fail()
      return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', ...answer } } }
    }
    if (url === '/api/v1/ai-invoke/active') {
      if (server.active === 'fail') return fail()
      return { data: { ok: true, active: server.active, group_id: params.group_id } }
    }
    throw new Error(`unexpected request ${url}`)
  })
})

const mounted: VueWrapper[] = []
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

async function settle() {
  for (let i = 0; i < 6; i += 1) await flushPromises()
}

async function mountExplorer(props: Record<string, unknown> = {}, pinia: Pinia = createPinia()) {
  setActivePinia(pinia)
  const wrapper = mount(DesignerFileExplorer, {
    props: { projectId: PID, flow: FLOW_A, groupLabel: '인증', ...props },
    global: { plugins: [i18n, pinia] },
  })
  mounted.push(wrapper)
  await settle()
  return wrapper
}

const rowPaths = (w: VueWrapper) =>
  w.findAll('[data-test="dfx-folder-row"],[data-test="dfx-file-row"]').map((r) => r.attributes('data-path'))
const row = (w: VueWrapper, path: string) => w.find(`[data-path="${path}"]`)
const badges = (w: VueWrapper) => w.findAll('[data-test="dfx-status"]').map((b) => b.text())

async function expand(w: VueWrapper, path: string) {
  await row(w, path).trigger('click')
}

describe('Designer File Explorer — tree, scope and interaction', () => {
  it('shows the active Flow tree with its scope, folders first, and expands/collapses by click and keys', async () => {
    const w = await mountExplorer()
    expect(w.get('[data-test="dfx-scope-name"]').text()).toBe('현재 Flow · 로그인 오류 수정')
    expect(w.get('[data-test="dfx-scope-group"]').text()).toBe('인증')
    expect(w.get('[data-test="dfx-scope-branch"]').text()).toBe('flow-0001')
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
    expect(w.get('[role="tree"]').exists()).toBe(true)

    await expand(w, 'src')
    expect(rowPaths(w)).toEqual(['docs', 'src', 'src/app.ts', 'src/new.ts', 'src/util.ts', 'README.md'])
    expect(row(w, 'src').attributes('aria-expanded')).toBe('true')
    expect(row(w, 'src/app.ts').attributes('aria-level')).toBe('2')

    await row(w, 'src').trigger('keydown', { key: 'ArrowLeft' })
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
    await row(w, 'docs').trigger('keydown', { key: 'ArrowRight' })
    expect(rowPaths(w)).toContain('docs/guide.md')
  })

  it('forwards select / open / context intents with project, group, commit and canMutate', async () => {
    const w = await mountExplorer()
    await row(w, 'README.md').trigger('click')
    const select = w.emitted('select')![0][0] as Record<string, any>
    expect(select).toMatchObject({ projectId: PID, groupId: FLOW_A.groupId, commit: 'c0ffee', canMutate: true })
    expect(select.node.path).toBe('README.md')
    expect(row(w, 'README.md').attributes('aria-selected')).toBe('true')

    await row(w, 'README.md').trigger('dblclick')
    await row(w, 'README.md').trigger('keydown', { key: 'Enter' })
    expect(w.emitted('open')).toHaveLength(2)
    await row(w, 'README.md').trigger('contextmenu')
    expect((w.emitted('context')![0][0] as Record<string, any>).node.path).toBe('README.md')
    // Opening is an intent only — the explorer never navigates or opens anything itself.
    expect(calls('/blob')).toHaveLength(0)
  })

  it('finds by name inside the loaded tree, revealing ancestors, and shows a no-result state', async () => {
    const w = await mountExplorer()
    const before = getRequest.mock.calls.length
    await w.get('[data-test="dfx-search-toggle"]').trigger('click')
    await w.get('[data-test="dfx-search-input"]').setValue('guide')
    expect(rowPaths(w)).toEqual(['docs', 'docs/guide.md'])
    await w.get('[data-test="dfx-search-input"]').setValue('zzz')
    expect(w.get('[data-test="dfx-empty"]').text()).toContain('"zzz"와 일치하는 파일이 없습니다.')
    expect(getRequest.mock.calls.length).toBe(before)
    // Closing the finder clears the query.
    await w.get('[data-test="dfx-search-toggle"]').trigger('click')
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
  })

  it('follows the current Project of features/project when projectId is omitted', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useProjectStore()
    const project: Project = { id: PID, name: 'Demo', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
    store.projects = [project]
    store.setCurrentProject(project)
    const w = await mountExplorer({ projectId: undefined }, pinia)
    expect(calls(`/api/v1/projects/${PID}/git/groups/${FLOW_A.groupId}/tree`)).toHaveLength(1)
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
  })
})

describe('Designer File Explorer — 변경됨 against the configured project Base', () => {
  it.each(['v0.2', 'main', 'release/2026'])('lists commits + worktree changes against Base %s, never the group work base', async (base) => {
    server.base = base
    const w = await mountExplorer()
    const changeCalls = calls('/changes')
    expect(changeCalls).toHaveLength(1)
    expect(changeCalls[0][1]).toEqual({ base })
    expect(w.get('[data-test="dfx-base-label"]').text()).toBe(`Base ${base} 대비`)
    expect(w.get('[data-test="dfx-changed-count"]').text()).toBe('3')

    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    // Only changed paths and their ancestors; the deletion the Flow already committed is
    // kept as a virtual row even though it is no longer in the HEAD tree.
    expect(rowPaths(w)).toEqual(['docs', 'src'])
    expect(row(w, 'src').get('[data-test="dfx-folder-changed"]').text()).toBe('2')
    await expand(w, 'src')
    await expand(w, 'docs')
    expect(rowPaths(w)).toEqual(['docs', 'docs/old.md', 'src', 'src/app.ts', 'src/new.ts'])
    expect(badges(w)).toEqual(['D', 'M', 'A'])

    // A deleted file cannot be opened, but its diff can — with the Base the list used.
    await row(w, 'docs/old.md').trigger('dblclick')
    expect(w.emitted('open')).toBeUndefined()
    await row(w, 'docs/old.md').get('[data-test="dfx-open-diff"]').trigger('click')
    expect(w.emitted('open-diff')![0][0]).toMatchObject({ status: 'D', baseBranch: base, groupId: FLOW_A.groupId })
  })

  it('shows the same Base-relative badges in the 전체 view', async () => {
    const w = await mountExplorer()
    await expand(w, 'src')
    expect(row(w, 'src/app.ts').get('[data-test="dfx-status"]').text()).toBe('M')
    expect(row(w, 'src/util.ts').find('[data-test="dfx-status"]').exists()).toBe(false)
  })

  it('rejects an answer computed against another branch (a server that ignores base)', async () => {
    server.base = 'main'
    server.changes = () => ({ base_branch: 'v0.2', changes: [{ path: 'src/app.ts', status: 'M' }] })
    const w = await mountExplorer()
    await expand(w, 'src')
    expect(badges(w)).toEqual([])
    expect(w.find('[data-test="dfx-changed-count"]').exists()).toBe(false)
    expect(w.get('[data-test="dfx-changed-unknown"]').text()).toBe('?')
    expect(w.get('[data-test="dfx-base-label"]').text()).toBe('Base 미확정')
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    const unresolved = w.get('[data-test="dfx-base-unresolved"]').text()
    expect(unresolved).toContain('main')
    expect(unresolved).toContain('v0.2')
    expect(w.find('[role="tree"]').exists()).toBe(false)
  })

  it('treats an answer that does not name its Base as unresolved', async () => {
    server.changes = () => ({ base_branch: null, changes: [{ path: 'src/app.ts', status: 'M' }] })
    const w = await mountExplorer()
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-base-unresolved"]').text()).toContain('어떤 Base branch와 비교했는지')
    expect(badges(w)).toEqual([])
  })

  it.each([null, '', '   '])('does not ask for changes when the project has no Base (%j) — not even the server main fallback', async (base) => {
    server.base = base
    const w = await mountExplorer()
    expect(calls('/changes')).toHaveLength(0)
    expect(w.get('[data-test="dfx-base-label"]').text()).toBe('Base 미확정')
    expect(badges(w)).toEqual([])
    expect(w.find('[data-test="dfx-changed-count"]').exists()).toBe(false)
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-base-unresolved"]').text()).toContain('Base branch가 설정되어 있지 않습니다')
  })

  it('does not compare when the server cannot say whether its Base is configured', async () => {
    server.base = 'main'
    server.statusShape = 'legacy'
    const w = await mountExplorer()
    expect(calls('/changes')).toHaveLength(0)
    expect(w.get('[data-test="dfx-base-label"]').text()).toBe('Base 미확정')
  })

  it('does not ask for changes when the project Git status cannot be read', async () => {
    server.base = 'fail'
    const w = await mountExplorer()
    expect(calls('/changes')).toHaveLength(0)
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-base-unresolved"]').text()).toContain('Git 상태를 읽지 못해')
  })

  it('shows a change-list failure as an error with retry, not as "no changes"', async () => {
    server.changes = () => 'fail'
    const w = await mountExplorer()
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.find('[data-test="dfx-changes-error"]').exists()).toBe(true)
    expect(w.find('[data-test="dfx-empty"]').exists()).toBe(false)
    server.changes = defaultServer().changes
    await w.get('[data-test="dfx-changes-error"] button').trigger('click')
    await settle()
    expect(rowPaths(w)).toEqual(['docs', 'src'])
  })

  it('says so when nothing changed against the Base', async () => {
    server.changes = (base) => ({ base_branch: base ?? null, changes: [] })
    const w = await mountExplorer()
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-empty"]').text()).toContain('Base v0.2 대비 변경된 파일이 없습니다.')
  })
})

describe('Designer File Explorer — readonly / permission / busy protection', () => {
  async function selectIntent(w: VueWrapper) {
    await row(w, 'README.md').trigger('click')
    const all = w.emitted('select')!
    return all[all.length - 1][0] as { canMutate: boolean }
  }

  it('is writable only with a writable slot, a confirmed idle AI state and no readonly/busy prop', async () => {
    const w = await mountExplorer()
    expect((await selectIntent(w)).canMutate).toBe(true)
    expect(w.find('[data-test="dfx-scope-readonly"]').exists()).toBe(false)
    expect(w.find('[data-test="dfx-scope-busy"]').exists()).toBe(false)
  })

  it.each([
    ['slot not writable', () => { server.slots = [{ group_id: FLOW_A.groupId, writable: false }] }, {}, 'readonly'],
    ['slot field missing (older server)', () => { server.slots = [{ group_id: FLOW_A.groupId }] }, {}, 'readonly'],
    ['no slot for this Flow', () => { server.slots = [] }, {}, 'readonly'],
    ['AI running on the Flow', () => { server.active = true }, {}, 'busy'],
    ['AI activity unknown', () => { server.active = 'fail' }, {}, 'readonly'],
    ['readonly prop', () => {}, { readonly: true }, 'readonly'],
    ['busy prop', () => {}, { busy: true }, 'busy'],
  ] as const)('locks mutation when %s', async (_name, arrange, props, flag) => {
    arrange()
    const w = await mountExplorer(props)
    expect((await selectIntent(w)).canMutate).toBe(false)
    expect(w.find(`[data-test="dfx-scope-${flag}"]`).exists()).toBe(true)
  })

  it('drops the previous writable state when switching to a Flow whose status cannot be read', async () => {
    const w = await mountExplorer()
    expect((await selectIntent(w)).canMutate).toBe(true)
    server.base = 'fail'
    await w.setProps({ flow: FLOW_B })
    await settle()
    expect(rowPaths(w)).toEqual(['lib'])
    await row(w, 'lib').trigger('click')
    await row(w, 'lib/search.ts').trigger('click')
    const all = w.emitted('select')!
    expect((all[all.length - 1][0] as { canMutate: boolean }).canMutate).toBe(false)
    expect(w.find('[data-test="dfx-scope-readonly"]').exists()).toBe(true)
  })

  it('drops write access when a refresh of the same Flow cannot confirm it', async () => {
    const w = await mountExplorer()
    expect((await selectIntent(w)).canMutate).toBe(true)
    server.base = 'fail'
    await w.get('[data-test="dfx-refresh"]').trigger('click')
    await settle()
    expect((await selectIntent(w)).canMutate).toBe(false)
  })
})

describe('Designer File Explorer — loading / error / empty states', () => {
  it('blocks with an error on initial load failure and recovers on retry', async () => {
    server.tree = 'fail'
    const w = await mountExplorer()
    expect(w.find('[data-test="dfx-load-error"]').exists()).toBe(true)
    expect(w.find('[role="tree"]').exists()).toBe(false)
    server.tree = TREE
    await w.get('[data-test="dfx-retry"]').trigger('click')
    await settle()
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
  })

  it('keeps the rendered tree when a refresh fails and reports it non-blockingly', async () => {
    const w = await mountExplorer()
    server.tree = 'fail'
    await w.get('[data-test="dfx-refresh"]').trigger('click')
    await settle()
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
    expect(w.find('[data-test="dfx-refresh-error"]').exists()).toBe(true)
    expect(w.find('[data-test="dfx-load-error"]').exists()).toBe(false)
    server.tree = TREE
    await w.get('[data-test="dfx-refresh-retry"]').trigger('click')
    await settle()
    expect(w.find('[data-test="dfx-refresh-error"]').exists()).toBe(false)
  })

  it('shows a loading state before the first answer', async () => {
    let release!: () => void
    server.treeGate = { [FLOW_A.groupId]: new Promise<void>((r) => { release = r }) }
    const w = await mountExplorer()
    expect(w.find('.ui-loading-state').exists()).toBe(true)
    release()
    await settle()
    expect(w.find('.ui-loading-state').exists()).toBe(false)
    expect(rowPaths(w)).toEqual(['docs', 'src', 'README.md'])
  })

  it('distinguishes no project, no Flow and an empty tree', async () => {
    const noProject = await mountExplorer({ projectId: null })
    expect(noProject.find('[data-test="dfx-no-project"]').exists()).toBe(true)
    const noFlow = await mountExplorer({ flow: null })
    expect(noFlow.find('[data-test="dfx-no-flow"]').exists()).toBe(true)
    expect(getRequest).not.toHaveBeenCalled()

    server.tree = []
    const empty = await mountExplorer()
    expect(empty.get('[data-test="dfx-empty"]').text()).toBe('이 Flow에 표시할 파일이 없습니다.')
  })

  it('never shows the previous Flow tree for a new Flow, and ignores a late answer for the old one', async () => {
    let releaseA!: () => void
    const w = await mountExplorer()
    server.treeGate = { [FLOW_A.groupId]: new Promise<void>((r) => { releaseA = r }) }
    await w.get('[data-test="dfx-refresh"]').trigger('click')
    await w.setProps({ flow: FLOW_B })
    await settle()
    expect(rowPaths(w)).toEqual(['lib'])
    releaseA()
    await settle()
    expect(rowPaths(w)).toEqual(['lib'])
    expect(w.get('[data-test="dfx-scope-name"]').text()).toBe('현재 Flow · 검색 개선')
  })
})
