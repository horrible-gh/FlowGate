/**
 * 0449 T0004 item 1.2 — FileExplorer holds the same initial-load / refresh-failure boundary
 * as GroupExplorer.
 *
 * It carried the identical shape of the bug: `silent` suppressed only the loading branch, so
 * a background refresh that failed replaced the whole file tree — and every create/upload
 * dialog the nodes own — with the blocking error screen.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileExplorer from '@main/components/FileExplorer.vue'
import { useExplorerStore } from '@main/stores/explorer'
import { useLayoutStore } from '@main/stores/layout'

const { getRequest, apiGet } = vi.hoisted(() => ({ getRequest: vi.fn(), apiGet: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))

function f(partial: Record<string, unknown>) {
  return { permissions: ['read'], ...partial }
}

const NODES = [
  f({ id: 'src', parent_id: null, type: 'folder', name: 'src', label: 'src', path: 'src' }),
  f({ id: 'leaf', parent_id: 'src', type: 'file', name: 'leaf.txt', label: 'leaf.txt', path: 'src/leaf.txt' }),
]

const timeout = () =>
  Object.assign(new Error('timeout of 130000ms exceeded'), { code: 'ECONNABORTED' })

async function mountExplorer() {
  useLayoutStore().setFileExplorerCollapsed(false)
  getRequest.mockResolvedValue({ data: { data: { nodes: NODES } } })
  apiGet.mockResolvedValue({ data: { status: { slots: [] } } })
  const wrapper = mount(FileExplorer, {
    props: { projectId: 'p', refreshToken: 0 },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  expect(wrapper.find('.tree-ul').exists()).toBe(true)
  return wrapper
}

/** One background refresh whose first GET and getTreeWithRetry's single retry both fail. */
async function failedRefresh(wrapper: any, token: number) {
  getRequest.mockReset()
  getRequest.mockRejectedValue(timeout())
  await wrapper.setProps({ refreshToken: token })
  await new Promise((r) => setTimeout(r, 900))
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
})

describe('FileExplorer — background refresh failure keeps the rendered tree', () => {
  it('keeps the file tree and reports the failure without blocking', async () => {
    const wrapper = await mountExplorer()

    await failedRefresh(wrapper, 1)

    expect(wrapper.find('.tree-ul').exists()).toBe(true)
    expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).toContain('src')
    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(true)
    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
  })

  it('recovers when the retry button’s request succeeds', async () => {
    const wrapper = await mountExplorer()
    await failedRefresh(wrapper, 1)

    getRequest.mockReset()
    getRequest.mockResolvedValue({
      data: { data: { nodes: [...NODES, f({ id: 'new', parent_id: null, type: 'folder', name: 'docs', label: 'docs', path: 'docs' })] } },
    })
    await wrapper.find('[data-test="file-explorer-refresh-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(false)
    expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).toContain('docs')
  })

  it('still blocks with the old error screen when the INITIAL load has nothing to keep', async () => {
    useLayoutStore().setFileExplorerCollapsed(false)
    getRequest.mockRejectedValue(timeout())
    apiGet.mockResolvedValue({ data: { status: { slots: [] } } })
    const wrapper = mount(FileExplorer, {
      props: { projectId: 'p', refreshToken: 0 },
      global: { plugins: [i18n] },
    })
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()

    expect(wrapper.find('.sdb-state--error').exists()).toBe(true)
    expect(wrapper.find('.tree-ul').exists()).toBe(false)
    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(false)
  })
})

/**
 * 0449 TR0005 rev1 — the OTHER way a background refresh could take the rendered tree away.
 *
 * `reload()` calls `loadGroupSlots()` before it fetches anything, and that call reads
 * git/status. rev0 let a failing git/status be swallowed: the slot list was emptied,
 * `selectedGroup` was reconciled to null, and `reload()` read the result as an ordinary
 * "we are on base" — so it fetched the BASE tree and swapped it in over the group-branch
 * tree that was on screen, with no refresh error shown. Nothing here goes through the
 * catch that rev0's tests exercised, which is why they all passed over it.
 *
 * A tree GET that would have SUCCEEDED is used throughout, so the only thing under test is
 * the git/status failure.
 */
const GROUP = 'p.default.0449'

const GROUP_NODES = [
  f({ id: 'g-src', parent_id: null, type: 'folder', name: 'group-src', label: 'group-src', path: 'group-src' }),
]
const BASE_NODES = [
  f({ id: 'b-src', parent_id: null, type: 'folder', name: 'base-src', label: 'base-src', path: 'base-src' }),
]

const SLOTS = [{ group_id: GROUP, branch: 'g', status: 'ready', writable: true }]

/** Routes each GET by URL so git/status can fail while the tree calls still succeed. */
function routeGets(opts: { gitStatusFails: boolean }) {
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/git/status')) {
      return opts.gitStatusFails
        ? Promise.reject(timeout())
        : Promise.resolve({ data: { status: { slots: SLOTS } } })
    }
    if (url.includes('/git/groups/')) {
      if (url.includes('/changes')) return Promise.resolve({ data: { data: { changes: [] } } })
      return Promise.resolve({ data: { data: { branch: 'g', commit: 'c1', nodes: GROUP_NODES } } })
    }
    return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
  })
}

async function mountOnGroupBranch() {
  useLayoutStore().setFileExplorerCollapsed(false)
  useExplorerStore().activeGroupBranch = GROUP
  routeGets({ gitStatusFails: false })
  apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })
  const wrapper = mount(FileExplorer, {
    props: { projectId: 'p', refreshToken: 0 },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  // Positive control: the group-branch tree really is what is on screen before we break
  // anything, so the assertions below are about it being KEPT, not about it never arriving.
  expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).toContain('group-src')
  return wrapper
}

describe('FileExplorer — a failing git/status must not silently swap the tree', () => {
  it('keeps the group-branch tree and never fetches the base tree', async () => {
    const wrapper = await mountOnGroupBranch()

    routeGets({ gitStatusFails: true })
    await wrapper.setProps({ refreshToken: 1 })
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()

    const labels = wrapper.findAll('.tree-lbl').map((x) => x.text())
    expect(labels).toContain('group-src')
    expect(labels).not.toContain('base-src')
    // The decisive one: rev0 issued this request and rendered its answer.
    const urls = getRequest.mock.calls.map((c: any[]) => String(c[0]))
    expect(urls.some((u) => u.includes('/files/tree'))).toBe(false)
  })

  it('reports the failure instead of passing it off as a successful refresh', async () => {
    const wrapper = await mountOnGroupBranch()

    routeGets({ gitStatusFails: true })
    await wrapper.setProps({ refreshToken: 1 })
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()

    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(true)
    // Non-blocking: the tree is still there, so this is not the blocking error screen.
    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
    expect(wrapper.find('.tree-ul').exists()).toBe(true)
  })

  it('keeps the group selected, so the header badge and write gate do not flip to base', async () => {
    const wrapper = await mountOnGroupBranch()
    expect(wrapper.find('.fx-readonly-badge').exists()).toBe(true)

    routeGets({ gitStatusFails: true })
    await wrapper.setProps({ refreshToken: 1 })
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()

    // rev0 nulled selectedGroup here, which took the badge with it and told the user they
    // were looking at base while the group's own files were still on screen.
    expect(wrapper.find('.fx-readonly-badge').exists()).toBe(true)
    expect((wrapper.find('.fx-group-select').element as HTMLSelectElement).value).toBe(GROUP)
  })

  it('the retry button recovers once git/status answers again', async () => {
    const wrapper = await mountOnGroupBranch()

    routeGets({ gitStatusFails: true })
    await wrapper.setProps({ refreshToken: 1 })
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()
    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(true)

    routeGets({ gitStatusFails: false })
    await wrapper.find('[data-test="file-explorer-refresh-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(false)
    expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).toContain('group-src')
  })

  it('a group that git/status says is really gone still drops away on a SUCCESSFUL refresh', async () => {
    // The guard must not turn into "selectedGroup can never be cleared". A refresh that
    // SUCCEEDS and no longer lists the slot is the server saying the worktree is finished,
    // and the explorer has to fall back to base — 0192 T0005 §2-a's behaviour, unchanged.
    const wrapper = await mountOnGroupBranch()

    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) return Promise.resolve({ data: { status: { slots: [] } } })
      return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
    })
    await wrapper.setProps({ refreshToken: 2 })
    await flushPromises()

    expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).toContain('base-src')
    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(false)
    expect(wrapper.find('.fx-readonly-badge').exists()).toBe(false)
  })
})

/**
 * 0449 TR0005 rev2 — the same git/status boundary on the FIRST load.
 *
 * rev1 closed it for the refresh path only. The mount watcher awaited `loadGroupSlots(pid)`
 * and threw the boolean away, so a git/status failure there fell through to the `else` branch
 * and fetched the BASE tree. With the tree GET succeeding — the ordinary case, since git/status
 * and the tree read fail independently — the explorer drew a full base tree with no error at
 * all, and on a remount that had PRESERVED `activeGroupBranch` the group's entry point (badge,
 * dropdown selection, write gate) was gone with it. That is a first load that reports a guess
 * as a result, which is what item 1.2 and completion criterion 1 separate the two states for.
 *
 * The tree GETs succeed throughout, so git/status is the only thing under test.
 */
describe('FileExplorer — a failing git/status on the FIRST load must not answer with base', () => {
  async function mountWithFailingGitStatus() {
    useLayoutStore().setFileExplorerCollapsed(false)
    // A remount that preserved the active group (the SSE explorerRefreshKey path).
    useExplorerStore().activeGroupBranch = GROUP
    routeGets({ gitStatusFails: true })
    apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })
    const wrapper = mount(FileExplorer, {
      props: { projectId: 'p', refreshToken: 0 },
      global: { plugins: [i18n] },
    })
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()
    return wrapper
  }

  it('never fetches the base tree it cannot know is the right one', async () => {
    const wrapper = await mountWithFailingGitStatus()

    const urls = getRequest.mock.calls.map((c: any[]) => String(c[0]))
    expect(urls.some((u) => u.includes('/git/status'))).toBe(true)
    // The decisive one: rev1 issued this and rendered its answer as a normal result.
    expect(urls.some((u) => u.includes('/files/tree'))).toBe(false)
    expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).not.toContain('base-src')
  })

  it('blocks with the initial-load error instead of a tree it could not verify', async () => {
    const wrapper = await mountWithFailingGitStatus()

    expect(wrapper.find('.sdb-state--error').exists()).toBe(true)
    expect(wrapper.find('.tree-ul').exists()).toBe(false)
    // Not the non-blocking strip: there is no rendered tree to keep on a first load.
    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(false)
  })

  it('keeps the preserved group, so the retry lands back on the group branch', async () => {
    const wrapper = await mountWithFailingGitStatus()

    // rev1 nulled selectedGroup inside the swallowed catch, so even a recovering retry came
    // back on base and the group entry point never returned without a full remount.
    routeGets({ gitStatusFails: false })
    await wrapper.find('.sdb-state--error button').trigger('click')
    await flushPromises()

    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
    expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).toContain('group-src')
    expect((wrapper.find('.fx-group-select').element as HTMLSelectElement).value).toBe(GROUP)
  })

  it('a retry with git/status STILL failing stays on the blocking error, even though the tree GET would succeed', async () => {
    // 0449 TR0005 rev2 review finding: the retry button calls reload(), whose bail-out on a
    // failed slot fetch only fired when a tree was already on screen (`silent`). On this
    // first-load error the explorer has nothing rendered, so `silent` is false and rev2 fell
    // through to the tree fetch regardless of `slotsOk` — a tree GET that answers on its own
    // (git/status and the tree read fail independently, per routeGets) cleared the blocking
    // error over a tree that was never verified against a working git/status.
    const wrapper = await mountWithFailingGitStatus()
    expect(wrapper.find('.sdb-state--error').exists()).toBe(true)

    // git/status is left failing; only the tree/group endpoints would succeed.
    await wrapper.find('.sdb-state--error button').trigger('click')
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()

    expect(wrapper.find('.sdb-state--error').exists()).toBe(true)
    expect(wrapper.find('.tree-ul').exists()).toBe(false)
    const urls = getRequest.mock.calls.map((c: any[]) => String(c[0]))
    expect(urls.some((u) => u.includes('/files/tree'))).toBe(false)
    expect(urls.some((u) => u.includes('/git/groups/') && !u.includes('/changes'))).toBe(false)
  })

  it('a first load whose git/status SUCCEEDS with no slots still draws the base tree', async () => {
    // Positive control. Without it, "never draw base on a first load" would also be green,
    // and that would break every project that genuinely has no group worktree.
    useLayoutStore().setFileExplorerCollapsed(false)
    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) return Promise.resolve({ data: { status: { slots: [] } } })
      return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
    })
    const wrapper = mount(FileExplorer, {
      props: { projectId: 'p', refreshToken: 0 },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    expect(wrapper.findAll('.tree-lbl').map((x) => x.text())).toContain('base-src')
    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
  })
})
