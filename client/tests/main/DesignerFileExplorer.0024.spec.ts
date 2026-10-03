import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import DesignerFileExplorer from '@main/components/designer/fileExplorer/DesignerFileExplorer.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { useExplorerStore } from '@main/stores/explorer'

// flowgate.v02.0024 T0003 — Designer File Explorer: Flow-scoped tree, rows, 전체/변경됨
// filter against the PROJECT's Base branch (never an assumed `main`), find, intents,
// readonly/busy gates, and loading / error / empty / refresh-failure states.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))

const PID = 'p1'
const GID = 'flowgate.v02.0024'
const FLOW = { groupId: GID, name: '로그인 오류 수정', branch: 'flow_0024' }

function n(id: string, parent: string | null, type: 'folder' | 'file', path: string) {
  const name = path.split('/').pop()!
  return { id, parent_id: parent, type, name, label: name, path, permissions: ['read'] }
}

const TREE = [
  n('1', null, 'folder', 'src'),
  n('2', '1', 'file', 'src/main.ts'),
  n('3', '1', 'file', 'src/util.ts'),
  n('4', null, 'file', 'README.md'),
  n('5', null, 'file', 'new.txt'),
]

type Server = {
  tree?: () => unknown
  status?: () => unknown
  changes?: (url: string) => unknown
}

let server: Server
let calls: string[]

function ok(data: unknown) {
  return Promise.resolve({ data })
}

function install(next: Server) {
  server = next
  calls = []
  getRequest.mockImplementation((url: string) => {
    calls.push(url)
    if (url.endsWith('/tree')) return server.tree ? server.tree() : ok({ data: { branch: 'flow_0024', commit: 'c1', nodes: TREE } })
    if (url.endsWith('/git/status')) {
      return server.status
        ? server.status()
        : ok({ ok: true, status: { enabled: true, base_branch: 'v0.2', slots: [{ group_id: GID, branch: 'flow_0024', status: 'none', merge_id: null, writable: true }] } })
    }
    if (url.includes('/changes')) {
      return server.changes
        ? server.changes(url)
        : ok({ data: {
          group_id: GID, branch: 'flow_0024', commit: 'c1', base_branch: requestedBase(url) ?? 'v0.2',
          changes: [
            { path: 'src/main.ts', status: 'M' },
            { path: 'new.txt', status: '?' },
            { path: 'old/gone.ts', status: 'D' },
          ],
        } })
    }
    return Promise.reject(new Error(`unexpected ${url}`))
  })
}

// The `base` query the adapter sends; the server echoes it as `base_branch`.
function requestedBase(url: string): string | null {
  return new URL(url, 'http://x').searchParams.get('base')
}

async function mountExplorer(props: Record<string, unknown> = {}) {
  const wrapper = mount(DesignerFileExplorer, {
    props: { projectId: PID, flow: FLOW, groupLabel: 'v02 · 0024', ...props },
    global: { plugins: [i18n], stubs: { AppIcon: true } },
  })
  await flushPromises()
  return wrapper
}

const paths = (w: ReturnType<typeof mount>) =>
  w.findAll('[data-test="dfx-folder-row"], [data-test="dfx-file-row"]').map((r) => r.attributes('data-path'))

const row = (w: ReturnType<typeof mount>, path: string) => w.get(`[data-path="${path}"]`)

describe('DesignerFileExplorer (0024 T0003)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
    vi.clearAllMocks()
    vi.useRealTimers()
    install({})
  })

  it('renders the active Flow scope and its tree; folders expand and collapse', async () => {
    const w = await mountExplorer()
    expect(w.get('[data-test="dfx-scope-name"]').text()).toContain('로그인 오류 수정')
    expect(calls.some((u) => u.includes(`/git/groups/${encodeURIComponent(GID)}/tree`))).toBe(true)
    expect(paths(w)).toEqual(['src', 'new.txt', 'README.md'])

    await row(w, 'src').trigger('click')
    expect(paths(w)).toEqual(['src', 'src/main.ts', 'src/util.ts', 'new.txt', 'README.md'])
    expect(row(w, 'src').attributes('aria-expanded')).toBe('true')

    await row(w, 'src').trigger('click')
    expect(paths(w)).toEqual(['src', 'new.txt', 'README.md'])
  })

  it('forwards select / open / open-diff intents without opening anything itself', async () => {
    const w = await mountExplorer()
    await row(w, 'src').trigger('click')
    await row(w, 'src/main.ts').trigger('click')
    expect(row(w, 'src/main.ts').attributes('aria-selected')).toBe('true')
    const select = w.emitted('select')![0][0] as any
    expect(select).toMatchObject({ projectId: PID, groupId: GID, commit: 'c1', canMutate: true })
    expect(select.node.path).toBe('src/main.ts')

    await row(w, 'src/main.ts').trigger('dblclick')
    expect((w.emitted('open')![0][0] as any).node.path).toBe('src/main.ts')

    await row(w, 'src/main.ts').get('[data-test="dfx-open-diff"]').trigger('click')
    expect(w.emitted('open-diff')![0][0]).toMatchObject({ status: 'M', baseBranch: 'v0.2' })
  })

  it('변경됨 compares against the configured Base (v0.2, not main) and lists M/A/D incl. committed deletions', async () => {
    const w = await mountExplorer()
    expect(w.get('[data-test="dfx-base-label"]').text()).toContain('v0.2')
    expect(w.get('[data-test="dfx-base-label"]').text()).not.toContain('main')
    expect(w.get('[data-test="dfx-changed-count"]').text()).toBe('3')
    expect(calls).toContain(`/api/v1/projects/${PID}/git/groups/${encodeURIComponent(GID)}/changes?base=v0.2`)

    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    // README.md / src/util.ts are unchanged and drop out; the committed deletion appears.
    expect(paths(w)).toEqual(['old', 'src', 'new.txt'])
    await row(w, 'src').trigger('click')
    await row(w, 'old').trigger('click')
    expect(paths(w)).toEqual(['old', 'old/gone.ts', 'src', 'src/main.ts', 'new.txt'])
    const status = (p: string) => row(w, p).get('[data-test="dfx-status"]').text()
    expect(status('src/main.ts')).toBe('M')
    expect(status('new.txt')).toBe('A')
    expect(status('old/gone.ts')).toBe('D')

    // A deleted file has nothing to open; its diff is still reachable.
    await row(w, 'old/gone.ts').trigger('dblclick')
    expect(w.emitted('open')).toBeUndefined()
    await row(w, 'old/gone.ts').get('[data-test="dfx-open-diff"]').trigger('click')
    expect(w.emitted('open-diff')![0][0]).toMatchObject({ status: 'D', baseBranch: 'v0.2' })

    await w.get('[data-test="dfx-filter-all"]').trigger('click')
    expect(paths(w)).toContain('README.md')
  })

  it('Base unconfigured: no comparison request, no badges, 변경됨 shows an unresolved state', async () => {
    install({ status: () => ok({ ok: true, status: { enabled: false, base_branch: null, slots: [] } }) })
    const w = await mountExplorer()
    expect(calls.some((u) => u.includes('/changes'))).toBe(false)
    expect(w.find('[data-test="dfx-status"]').exists()).toBe(false)
    expect(w.get('[data-test="dfx-base-label"]').text()).toBe('Base unresolved')

    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.find('[data-test="dfx-tree"]').exists()).toBe(false)
    expect(w.get('[data-test="dfx-state-error"]').text()).toContain('Base branch could not be determined')
  })

  it('Base unavailable (git status failed) is unresolved, not a guessed comparison', async () => {
    install({ status: () => Promise.reject(new Error('boom')) })
    const w = await mountExplorer()
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-state-error"]').text()).toContain('Git status could not be read')
    expect(calls.some((u) => u.includes('/changes'))).toBe(false)
  })

  it('a change set that does not name its Base is rejected', async () => {
    install({ changes: () => ok({ data: { group_id: GID, branch: 'b', commit: 'c1', changes: [{ path: 'src/main.ts', status: 'M' }] } }) })
    const w = await mountExplorer()
    expect(w.find('[data-test="dfx-status"]').exists()).toBe(false)
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-state-error"]').text()).toContain('did not say which Base')
  })

  it('project Base main, group pinned to v0.2: 변경됨 lists the Flow against main, not the work base', async () => {
    // This group's real shape: the project Base is `main` while the group forked from the
    // rolling `v0.2`. The server answers `/changes` for the branch named in `base`, and
    // without it for the group's own work base — emulate both.
    install({
      status: () => ok({ ok: true, status: { enabled: true, base_branch: 'main', slots: [{ group_id: GID, branch: 'flow_0024', status: 'none', merge_id: null, writable: true }] } }),
      changes: (url) => {
        const base = requestedBase(url) ?? 'v0.2'
        const own = [{ path: 'src/main.ts', status: 'M' }, { path: 'new.txt', status: '?' }]
        const fromV02 = [{ path: 'src/util.ts', status: 'A' }, { path: 'README.md', status: 'M' }]
        return ok({ data: {
          group_id: GID, branch: 'flow_0024', commit: 'c1', base_branch: base,
          changes: base === 'main' ? [...fromV02, ...own] : own,
        } })
      },
    })
    const w = await mountExplorer()
    expect(calls).toContain(`/api/v1/projects/${PID}/git/groups/${encodeURIComponent(GID)}/changes?base=main`)
    expect(w.get('[data-test="dfx-base-label"]').text()).toContain('main')
    expect(w.get('[data-test="dfx-base-label"]').text()).not.toContain('v0.2')
    expect(w.get('[data-test="dfx-changed-count"]').text()).toBe('4')

    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    await row(w, 'src').trigger('click')
    expect(paths(w)).toEqual(['src', 'src/main.ts', 'src/util.ts', 'new.txt', 'README.md'])
    const status = (p: string) => row(w, p).get('[data-test="dfx-status"]').text()
    expect(status('src/util.ts')).toBe('A')
    expect(status('README.md')).toBe('M')
    await row(w, 'src/util.ts').get('[data-test="dfx-open-diff"]').trigger('click')
    expect(w.emitted('open-diff')![0][0]).toMatchObject({ status: 'A', baseBranch: 'main' })

    // A Project-Base answer is not what the Dashboard badges mean: their cache is untouched.
    expect(useExplorerStore().groupChangedFiles).toEqual({})
  })

  it('a server that ignores base and answers for another branch is rejected as a mismatch', async () => {
    install({ changes: () => ok({ data: { group_id: GID, branch: 'b', commit: 'c1', base_branch: 'main', changes: [{ path: 'src/main.ts', status: 'M' }] } }) })
    const w = await mountExplorer()
    expect(w.find('[data-test="dfx-status"]').exists()).toBe(false)
    expect(w.find('[data-test="dfx-changed-count"]').exists()).toBe(false)
    expect(w.get('[data-test="dfx-base-label"]').text()).toBe('Base unresolved')
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    const text = w.get('[data-test="dfx-state-error"]').text()
    expect(text).toContain('main')
    expect(text).toContain('v0.2')
    expect(w.find('[data-test="dfx-tree"]').exists()).toBe(false)
  })

  it('changes request failure keeps 전체 usable and shows an error under 변경됨', async () => {
    install({ changes: () => Promise.reject(new Error('500')) })
    const w = await mountExplorer()
    expect(paths(w)).toEqual(['src', 'new.txt', 'README.md'])
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-state-error"]').text()).toContain('changed file list')
  })

  it('찾기 filters the current tree by name and reveals the matches', async () => {
    const w = await mountExplorer()
    await w.get('[data-test="dfx-search-toggle"]').trigger('click')
    await w.get('[data-test="dfx-search-input"]').setValue('util')
    expect(paths(w)).toEqual(['src', 'src/util.ts'])
    await w.get('[data-test="dfx-search-input"]').setValue('zzz')
    expect(w.get('[data-test="dfx-state-empty"]').text()).toContain('zzz')
    // Closing find clears the query and restores the tree.
    await w.get('[data-test="dfx-search-toggle"]').trigger('click')
    expect(paths(w)).toEqual(['src', 'new.txt', 'README.md'])
  })

  it('readonly gates: a slot without a live worktree, the readonly prop, and an AI run each block mutation', async () => {
    install({ status: () => ok({ ok: true, status: { enabled: true, base_branch: 'v0.2', slots: [{ group_id: GID, branch: 'b', status: 'none', merge_id: null, writable: false }] } }) })
    let w = await mountExplorer()
    expect(w.find('[data-test="dfx-scope-readonly"]').exists()).toBe(true)
    await row(w, 'README.md').trigger('contextmenu')
    expect((w.emitted('context')![0][0] as any).canMutate).toBe(false)

    install({})
    w = await mountExplorer({ readonly: true })
    await row(w, 'README.md').trigger('click')
    expect((w.emitted('select')![0][0] as any).canMutate).toBe(false)

    install({})
    w = await mountExplorer()
    expect(w.find('[data-test="dfx-scope-readonly"]').exists()).toBe(false)
    useAiInvokeRunsStore().trackStarted({ run_id: 'aiv_0024', group_id: GID, doc_ref: `${GID}.0003-T`, status: 'running' } as any)
    await flushPromises()
    expect(w.find('[data-test="dfx-scope-busy"]').exists()).toBe(true)
    await row(w, 'README.md').trigger('click')
    expect((w.emitted('select')![0][0] as any).canMutate).toBe(false)
  })

  it('switching to a scope whose git status fails drops the previous writable slot', async () => {
    let w = await mountExplorer()
    await row(w, 'README.md').trigger('click')
    expect((w.emitted('select')![0][0] as any).canMutate).toBe(true)

    install({ status: () => Promise.reject(new Error('boom')) })
    await w.setProps({ flow: { groupId: 'flowgate.v02.0025', name: 'Other', branch: 'flow_0025' } })
    await flushPromises()
    expect(w.find('[data-test="dfx-scope-readonly"]').exists()).toBe(true)
    await row(w, 'README.md').trigger('click')
    expect((w.emitted('select')!.at(-1)![0] as any).canMutate).toBe(false)

    // Same scope, refresh whose git status fails: write access is not kept either.
    install({})
    w = await mountExplorer()
    install({ status: () => Promise.reject(new Error('boom')) })
    await w.get('[data-test="dfx-refresh"]').trigger('click')
    await flushPromises()
    await row(w, 'README.md').trigger('contextmenu')
    expect((w.emitted('context')!.at(-1)![0] as any).canMutate).toBe(false)
  })

  it('initial load failure blocks with a retry; retry recovers', async () => {
    let fail = true
    install({ tree: () => (fail ? Promise.reject(new Error('x')) : ok({ data: { branch: 'b', commit: 'c2', nodes: TREE } })) })
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    const w = mount(DesignerFileExplorer, {
      props: { projectId: PID, flow: FLOW },
      global: { plugins: [i18n], stubs: { AppIcon: true } },
    })
    await vi.advanceTimersByTimeAsync(1000) // the store's single tree retry (800ms)
    await flushPromises()
    vi.useRealTimers()
    expect(w.find('[data-test="dfx-state-error"]').exists()).toBe(true)
    expect(w.find('[data-test="dfx-tree"]').exists()).toBe(false)

    fail = false
    await w.get('[data-test="dfx-state-retry"]').trigger('click')
    await flushPromises()
    expect(paths(w)).toEqual(['src', 'new.txt', 'README.md'])
  })

  it('a failed refresh keeps the rendered tree and shows a non-blocking strip', async () => {
    const w = await mountExplorer()
    install({ tree: () => Promise.reject(new Error('x')) })
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    const done = w.get('[data-test="dfx-refresh"]').trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    await done
    await flushPromises()
    vi.useRealTimers()
    expect(w.find('[data-test="dfx-refresh-error"]').exists()).toBe(true)
    expect(paths(w)).toEqual(['src', 'new.txt', 'README.md'])
  })

  it('empty tree, no Flow and no project each render their own empty state', async () => {
    install({
      tree: () => ok({ data: { branch: 'b', commit: 'c1', nodes: [] } }),
      changes: () => ok({ data: { group_id: GID, branch: 'b', commit: 'c1', base_branch: 'v0.2', changes: [] } }),
    })
    let w = await mountExplorer()
    expect(w.get('[data-test="dfx-state-empty"]').text()).toContain('no files')
    await w.get('[data-test="dfx-filter-changed"]').trigger('click')
    expect(w.get('[data-test="dfx-state-empty"]').text()).toContain('v0.2')

    install({})
    w = await mountExplorer({ flow: null })
    expect(w.get('[data-test="dfx-state-empty"]').text()).toContain('No active Flow')
    expect(calls).toEqual([])

    w = await mountExplorer({ projectId: null })
    expect(w.get('[data-test="dfx-state-empty"]').text()).toContain('Select a project')
  })

  it('switching Flow reloads that Flow and drops the previous tree', async () => {
    const w = await mountExplorer()
    expect(paths(w)).toContain('README.md')
    install({ tree: () => ok({ data: { branch: 'b2', commit: 'c9', nodes: [n('9', null, 'file', 'other.ts')] } }) })
    await w.setProps({ flow: { groupId: 'flowgate.v02.0025', name: 'Other', branch: 'flow_0025' } })
    await flushPromises()
    expect(calls.some((u) => u.includes(encodeURIComponent('flowgate.v02.0025')))).toBe(true)
    expect(paths(w)).toEqual(['other.ts'])
  })
})
