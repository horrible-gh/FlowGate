// flowgate.default.0615 T0004 — File Explorer "브랜치" selector must show ordinary
// local branches (Branch Manager, kind=local) alongside the base branch and FlowGate
// group slots, let one be selected with no group slots present at all, read its
// committed tree checkout-free and read-only, dedup against internal_slot group
// branches, and react immediately to Branch Manager's create/delete broadcast.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileExplorer from '@main/components/FileExplorer.vue'
import FileTreeNode from '@main/components/FileTreeNode.vue'
import { useLayoutStore } from '@main/stores/layout'

const { getRequest, apiGet } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  apiGet: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(async () => []), uploadFiles: vi.fn() }),
}))

function f(partial: Record<string, unknown>) {
  return { permissions: ['read'], ...partial }
}

const BASE_NODES = [
  f({ id: 'b1', parent_id: null, type: 'file', name: 'base.md', label: 'base.md', path: 'base.md' }),
]
const LOCAL_NODES = [
  f({ id: 'l1', parent_id: null, type: 'file', name: 'feature.md', label: 'feature.md', path: 'feature.md' }),
]

const NO_GROUP_CATALOG = {
  ok: true,
  base_branch: 'main',
  default_merge_target: null,
  branches: [
    { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    { name: 'test-branch', kind: 'local', can_delete: true, can_be_create_source: true },
  ],
}

/** Routes each GET by URL; group slots always empty unless overridden. */
function routeGets(opts: {
  catalog?: typeof NO_GROUP_CATALOG
  slots?: Array<{ group_id: string; branch: string; status: string; writable?: boolean }>
  catalogFails?: boolean
  baseTreeFails?: boolean
} = {}) {
  const catalog = opts.catalog ?? NO_GROUP_CATALOG
  const slots = opts.slots ?? []
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/git/status')) {
      return Promise.resolve({ data: { status: { slots } } })
    }
    if (url.includes('/git/branches/tree')) {
      return Promise.resolve({ data: { data: { branch: 'test-branch', commit: 'c1', nodes: LOCAL_NODES } } })
    }
    if (url.includes('/git/branches')) {
      return opts.catalogFails ? Promise.reject(new Error('catalog boom')) : Promise.resolve({ data: catalog })
    }
    if (url.includes('/git/groups/')) {
      if (url.endsWith('/changes')) return Promise.resolve({ data: { data: { changes: [] } } })
      return Promise.resolve({ data: { data: { branch: 'g', commit: 'gc1', nodes: [] } } })
    }
    if (opts.baseTreeFails) return Promise.reject(new Error('base tree boom'))
    return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
  })
  apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })
}

// rev4 -- no test here ever unmounted its FileExplorer, so every mount's
// `window.addEventListener('fg:git_branches_changed', ...)` (and the
// `fg:git_pending_changed` one) stayed live past its own test. That was
// invisible as long as assertions only inspected the CURRENT test's own
// wrapper, but the two new rev4 generation-race tests below assert exact
// request counts on the shared getRequest mock -- and every still-mounted
// leftover instance from an earlier test also reacts to the same
// window.dispatchEvent broadcast, adding its own extra fetch calls. Tracking
// and unmounting every wrapper this file creates removes that ambient noise
// for everyone, not just the new tests.
const mountedWrappers: Array<{ unmount: () => void }> = []

async function mountExplorer() {
  useLayoutStore().setFileExplorerCollapsed(false)
  const wrapper = mount(FileExplorer, {
    props: { projectId: 'p' },
    global: { plugins: [i18n] },
  })
  mountedWrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
  routeGets()
})

afterEach(() => {
  vi.useRealTimers()
  while (mountedWrappers.length) mountedWrappers.pop()!.unmount()
})

describe('FileExplorer ordinary local branch selector (0615 T0004)', () => {
  it('shows the branch selector with group slots empty, once a local branch exists', async () => {
    const wrapper = await mountExplorer()
    const select = wrapper.find('.fx-group-select')
    expect(select.exists()).toBe(true)
    const options = select.findAll('option').map((o) => o.text())
    expect(options).toContain('test-branch')
  })

  it('hides the selector entirely when there are neither local branches nor group slots', async () => {
    routeGets({ catalog: { ok: true, base_branch: 'main', default_merge_target: null, branches: [
      { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    ] } })
    const wrapper = await mountExplorer()
    expect(wrapper.find('.fx-group-select').exists()).toBe(false)
  })

  it('reads the branch tree checkout-free and read-only when selected', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('/git/branches/tree?branch=test-branch'),
    )
    const labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('feature.md')
    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(true)
    expect(wrapper.findComponent(FileTreeNode).props('readonly')).toBe(true)
    expect(wrapper.findComponent(FileTreeNode).props('localBranch')).toBe('test-branch')
  })

  it('blocks mutation entirely on a selected local branch (no create/upload in the root menu)', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()

    await wrapper.get('.tree-node').trigger('contextmenu')
    await flushPromises()
    const labels = wrapper.findAll('.ctx-item, [class*=\"ctx\"]').map((n) => n.text())
    expect(labels.join('|')).not.toContain('New Folder')
  })

  it('does not duplicate a branch that is both a local entry and a live group slot', async () => {
    // T0004 §2.2 — an internal_slot branch shows once, as the group option, never
    // ALSO as a local-branch option.
    routeGets({
      catalog: {
        ok: true, base_branch: 'main', default_merge_target: null,
        branches: [
          { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
          { name: 'fg-0327', kind: 'internal_slot', can_delete: false, can_be_create_source: false, connected_group_id: 'flowgate.default.0327' },
        ],
      },
      slots: [{ group_id: 'flowgate.default.0327', branch: 'fg-0327', status: 'none', writable: true }],
    })
    const wrapper = await mountExplorer()
    const select = wrapper.find('.fx-group-select')
    const values = select.findAll('option').map((o) => (o.element as HTMLOptionElement).value)
    expect(values.filter((v) => v === 'local:fg-0327')).toHaveLength(0)
    expect(values.filter((v) => v === 'flowgate.default.0327')).toHaveLength(1)
  })

  it('excludes remote-only branches from the selector', async () => {
    routeGets({
      catalog: {
        ok: true, base_branch: 'main', default_merge_target: null,
        branches: [
          { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
          { name: 'test-branch', kind: 'local', can_delete: true, can_be_create_source: true },
          { name: 'old-remote', kind: 'remote_only', can_delete: false, can_be_create_source: false },
        ],
      },
    })
    const wrapper = await mountExplorer()
    const options = wrapper.find('.fx-group-select').findAll('option').map((o) => o.text())
    expect(options).not.toContain('old-remote')
  })

  it('refreshes the selector when Branch Manager broadcasts a create, without a remount', async () => {
    routeGets({ catalog: { ok: true, base_branch: 'main', default_merge_target: null, branches: [
      { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    ] } })
    const wrapper = await mountExplorer()
    expect(wrapper.find('.fx-group-select').exists()).toBe(false)

    routeGets({ catalog: NO_GROUP_CATALOG })
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', { detail: { project: 'p' } }))
    await flushPromises()

    const options = wrapper.find('.fx-group-select').findAll('option').map((o) => o.text())
    expect(options).toContain('test-branch')
  })

  it('falls back to base and drops the stale tree when the selected branch is deleted', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('feature.md')

    // Branch Manager deleted it: the next catalog fetch no longer lists it.
    routeGets({ catalog: { ok: true, base_branch: 'main', default_merge_target: null, branches: [
      { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    ] } })
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', {
      detail: { project: 'p', action: 'delete', branch: 'test-branch' },
    }))
    await flushPromises()

    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(false)
    const labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('base.md')
    expect(labels).not.toContain('feature.md')
  })

  it('rev2: drops the stale selection even when the follow-up catalog GET fails', async () => {
    // rev1 rejection: `loadBranchCatalog()` swallows its own failure, so if it is
    // the thing deciding whether the deleted branch was dropped, a failing catalog
    // GET leaves the deleted branch selected and its tree on screen forever. The
    // event now carries the deleted branch itself, so invalidation no longer
    // depends on this fetch succeeding.
    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('feature.md')

    routeGets({ catalogFails: true })
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', {
      detail: { project: 'p', action: 'delete', branch: 'test-branch' },
    }))
    await flushPromises()

    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(false)
    const labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('base.md')
    expect(labels).not.toContain('feature.md')

    // rev2 rejection: the follow-up catalog GET failing preserved the OLD catalog
    // wholesale, so the deleted branch kept showing up as a ghost option in the
    // selector even though the selection/tree itself had already fallen back to
    // base above. The deleted branch must be stripped out of the selector's own
    // options independent of that fetch's outcome.
    const optionValues = wrapper.findAll('.fx-group-select option').map((o) => o.attributes('value'))
    expect(optionValues).not.toContain('local:test-branch')
  })

  it('rev2: shows the blocking error instead of the deleted branch tree when the base fallback fetch also fails', async () => {
    // rev1 rejection: a delete-fallback reload switches source (local branch ->
    // base). Treating that as a "silent" background refresh let a failed base
    // fetch preserve the OLD local branch's nodes under a base selector/badge --
    // the exact stale-tree bug. renderedSource tracking must make this reload
    // non-silent, so a failure here blocks instead of preserving the wrong tree.
    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('feature.md')

    routeGets({
      catalog: { ok: true, base_branch: 'main', default_merge_target: null, branches: [
        { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
      ] },
      baseTreeFails: true,
    })
    // explorerStore.fetchFileTree retries once after an 800ms backoff (0283 T0004)
    // before giving up; fake timers let this test observe the eventual failure
    // without a real 800ms wait.
    vi.useFakeTimers()
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', {
      detail: { project: 'p', action: 'delete', branch: 'test-branch' },
    }))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(wrapper.find('.sdb-state--error').exists()).toBe(true)
    expect(wrapper.find('.tree-ul').exists()).toBe(false)
    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(false)
  })

  it('rev2: a stale mount-time catalog response does not resurrect a branch a newer create response just removed', async () => {
    // rev1 rejection: loadBranchCatalog had no generation guard, so whichever
    // response lands LAST wins even if it was issued first. Mount issues its own
    // catalog fetch; here it resolves AFTER the create broadcast's own fetch, and
    // must not overwrite that newer, more-current answer.
    let resolveMountCatalog!: (v: unknown) => void
    const mountCatalogPromise = new Promise((resolve) => { resolveMountCatalog = resolve })
    let branchesCalls = 0
    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) return Promise.resolve({ data: { status: { slots: [] } } })
      if (url.includes('/git/branches/tree')) {
        return Promise.resolve({ data: { data: { branch: 'test-branch', commit: 'c1', nodes: LOCAL_NODES } } })
      }
      if (url.includes('/git/branches')) {
        branchesCalls += 1
        // Call #1 is mount's own fetch -- held open. Call #2 is the create
        // broadcast's fetch -- resolves immediately with the new branch already in it.
        return branchesCalls === 1 ? mountCatalogPromise : Promise.resolve({ data: NO_GROUP_CATALOG })
      }
      return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
    })
    apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })

    const wrapper = await mountExplorer() // mount's catalog fetch is issued but stuck open

    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', {
      detail: { project: 'p', action: 'create', branch: 'test-branch' },
    }))
    await flushPromises()
    expect(wrapper.find('.fx-group-select').findAll('option').map((o) => o.text())).toContain('test-branch')

    // The stale mount response FINALLY resolves, with an older catalog that does
    // not have the branch yet -- it must not erase the newer answer above.
    resolveMountCatalog({ data: { ok: true, base_branch: 'main', default_merge_target: null, branches: [
      { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    ] } })
    await flushPromises()

    expect(wrapper.find('.fx-group-select').findAll('option').map((o) => o.text())).toContain('test-branch')
  })

  it('rev2: a stale catalog/tree response for the previous project does not leak onto the new one', async () => {
    // rev1 rejection: "project A 요청이 project B 전환 뒤 완료되면 A의 option이
    // B 화면을 덮을 수 있습니다." project A's catalog fetch is held open past the
    // switch to project B; B has no local branches at all, and A's late response
    // must not resurrect A's branch option (or A's tree) on B's screen.
    let resolveProjectACatalog!: (v: unknown) => void
    const projectACatalogPromise = new Promise((resolve) => { resolveProjectACatalog = resolve })
    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) return Promise.resolve({ data: { status: { slots: [] } } })
      if (url.includes('/git/branches/tree')) {
        return Promise.resolve({ data: { data: { branch: 'test-branch', commit: 'c1', nodes: LOCAL_NODES } } })
      }
      if (url.includes('/git/branches')) return projectACatalogPromise
      return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
    })
    apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })

    useLayoutStore().setFileExplorerCollapsed(false)
    const wrapper = mount(FileExplorer, {
      props: { projectId: 'project-a' },
      global: { plugins: [i18n] },
    })
    mountedWrappers.push(wrapper)
    await flushPromises() // project A's catalog fetch is in flight and stuck open

    routeGets({ catalog: { ok: true, base_branch: 'main', default_merge_target: null, branches: [
      { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    ] } })
    await wrapper.setProps({ projectId: 'project-b' })
    await flushPromises()
    expect(wrapper.find('.fx-group-select').exists()).toBe(false)

    resolveProjectACatalog({ data: NO_GROUP_CATALOG })
    await flushPromises()

    expect(wrapper.find('.fx-group-select').exists()).toBe(false)
  })

  it('ignores a broadcast for a different project', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()

    const before = getRequest.mock.calls.length
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', { detail: { project: 'other-project' } }))
    await flushPromises()
    expect(getRequest.mock.calls.length).toBe(before)
  })

  it('rev4: an old branchCatalog request already in flight when a delete arrives does not resurrect the deleted branch, even after it resolves late and the fresh catalog fetch fails', async () => {
    // rev3 rejection: delete filters branchCatalog synchronously, but never
    // invalidated whatever loadBranchCatalog() call was already in flight before
    // the event arrived. If that OLD request resolves after the filter -- and the
    // delete handler's own fresh catalog fetch then fails -- the old response was
    // still treated as the latest word and reapplied the pre-delete catalog,
    // resurrecting the deleted branch with no further event to clear it again.
    // Includes a SECOND local branch that survives the delete, so the selector
    // itself stays on screen afterward (with only test-branch removed) instead
    // of disappearing entirely -- letting the assertions below inspect its
    // remaining options directly.
    const TWO_BRANCH_CATALOG = {
      ok: true,
      base_branch: 'main',
      default_merge_target: null,
      branches: [
        { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
        { name: 'test-branch', kind: 'local', can_delete: true, can_be_create_source: true },
        { name: 'keep-branch', kind: 'local', can_delete: true, can_be_create_source: true },
      ],
    }
    let resolveOldCatalog!: (v: unknown) => void
    const oldCatalogPromise = new Promise((resolve) => { resolveOldCatalog = resolve })
    let resolveDeleteStatus!: (v: unknown) => void
    const deleteStatusPromise = new Promise((resolve) => { resolveDeleteStatus = resolve })
    let statusCalls = 0
    let branchesCalls = 0
    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) {
        statusCalls += 1
        // #1-#3: mount, the selection's own reload(), and the manual refresh's
        // reload() -- all resolve normally. #4: the delete handler's OWN reload()
        // (its loadGroupSlots call) -- held open. This keeps that reload from
        // ever reaching its own loadBranchCatalog() call (and thus from bumping
        // branchCatalogRequestSeq itself), isolating the narrow window the fix
        // actually has to cover: the gap between the delete handler's synchronous
        // work and its own reload() getting far enough to issue a fresh fetch.
        if (statusCalls <= 3) return Promise.resolve({ data: { status: { slots: [] } } })
        return deleteStatusPromise
      }
      if (url.includes('/git/branches/tree')) {
        return Promise.resolve({ data: { data: { branch: 'test-branch', commit: 'c1', nodes: LOCAL_NODES } } })
      }
      if (url.includes('/git/branches')) {
        branchesCalls += 1
        // #1: mount's own catalog fetch. #2: the selection's own reload() catalog
        // fetch -- both resolve normally so the branch is selected and rendered to
        // begin with. #3: a manual refresh's catalog fetch -- held open past the
        // delete (the "old" in-flight request). #4+: the delete handler's own
        // follow-up catalog fetch -- fails (reached only once deleteStatusPromise
        // resolves below).
        if (branchesCalls <= 2) return Promise.resolve({ data: TWO_BRANCH_CATALOG })
        if (branchesCalls === 3) return oldCatalogPromise
        return Promise.reject(new Error('fresh catalog boom'))
      }
      return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
    })
    apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })

    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('feature.md')

    // The refresh button starts a second, independent catalog GET (#3) -- left
    // pending while the branch is still selected.
    await wrapper.findAll('.sdb-act-btn')[0].trigger('click')
    await flushPromises()
    expect(branchesCalls).toBe(3)

    // Branch Manager deletes the selected branch. The handler's synchronous
    // filter runs immediately; its own reload() starts but stalls on
    // loadGroupSlots (deleteStatusPromise), so it has NOT yet reached its own
    // loadBranchCatalog() call -- the fresh fetch that would otherwise also
    // bump branchCatalogRequestSeq and mask a missing handler-level bump.
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', {
      detail: { project: 'p', action: 'delete', branch: 'test-branch' },
    }))
    await flushPromises()
    expect(statusCalls).toBe(4)
    expect(branchesCalls).toBe(3)

    let optionValues = wrapper.find('.fx-group-select').findAll('option')
      .map((o) => (o.element as HTMLOptionElement).value)
    expect(optionValues).not.toContain('local:test-branch')
    // keep-branch survives, so the selector itself is still on screen -- the
    // assertions above are checking a real filter, not the whole control hiding.
    expect(optionValues).toContain('local:keep-branch')

    // The OLD catalog request (#3, started before the delete) finally resolves
    // now, WHILE the delete's own reload() is still stalled before its own fresh
    // fetch. With no handler-level seq bump, this old response would still read
    // as the latest word at this exact instant and reapply the pre-delete
    // catalog, resurrecting the deleted branch with no further event to clear it.
    resolveOldCatalog({ data: TWO_BRANCH_CATALOG })
    await flushPromises()

    optionValues = wrapper.find('.fx-group-select').findAll('option')
      .map((o) => (o.element as HTMLOptionElement).value)
    expect(optionValues).not.toContain('local:test-branch')
    expect(optionValues).toContain('local:keep-branch')

    // Let the delete's own reload() proceed: its fresh catalog fetch (#4) fails
    // per the mock, and it falls back to rendering the base tree.
    resolveDeleteStatus({ data: { status: { slots: [] } } })
    await flushPromises()

    optionValues = wrapper.find('.fx-group-select').findAll('option')
      .map((o) => (o.element as HTMLOptionElement).value)
    expect(optionValues).not.toContain('local:test-branch')
    expect(optionValues).toContain('local:keep-branch')
    const labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('base.md')
    expect(labels).not.toContain('feature.md')
  })

  it('rev4: an old local-branch tree request pending when its branch is deleted must not overwrite the completed base fallback when it finally resolves', async () => {
    // rev3 rejection: reload() had no generation guard at all. A reload for the
    // local branch (started before the delete, e.g. a manual refresh while still
    // selected) can still be awaiting its own tree fetch when the delete's base
    // fallback reload starts, runs, and finishes. If the old local-branch tree
    // response then arrives late, nothing stopped it from overwriting the
    // now-rendered base tree with the stale local-branch nodes -- and re-deriving
    // the render target from live state at that point would even mislabel it as a
    // base render, since selectedLocalBranch was already back to null by then.
    let resolveOldTree!: (v: unknown) => void
    const oldTreePromise = new Promise((resolve) => { resolveOldTree = resolve })
    let treeCalls = 0
    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) return Promise.resolve({ data: { status: { slots: [] } } })
      if (url.includes('/git/branches/tree')) {
        treeCalls += 1
        // #1: the initial selection's own tree fetch -- resolves normally so
        // feature.md renders to begin with. #2: a manual refresh's tree fetch,
        // issued while still selected -- held open (the "old" in-flight request).
        if (treeCalls === 1) {
          return Promise.resolve({ data: { data: { branch: 'test-branch', commit: 'c1', nodes: LOCAL_NODES } } })
        }
        return oldTreePromise
      }
      if (url.includes('/git/branches')) return Promise.resolve({ data: NO_GROUP_CATALOG })
      return Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
    })
    apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })

    const wrapper = await mountExplorer()
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('feature.md')

    // The refresh button starts a second reload for the SAME selected branch --
    // its own tree fetch (#2) is left pending.
    await wrapper.findAll('.sdb-act-btn')[0].trigger('click')
    await flushPromises()
    expect(treeCalls).toBe(2)

    // Branch Manager deletes the selected branch while that old tree request is
    // still in flight. This starts a NEW reload that falls back to base and runs
    // to completion (its own tree fetch hits the unconditional base-tree branch
    // of the mock above, which resolves immediately).
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', {
      detail: { project: 'p', action: 'delete', branch: 'test-branch' },
    }))
    await flushPromises()

    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(false)
    let labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('base.md')
    expect(labels).not.toContain('feature.md')

    // The OLD local-branch tree request (#2, started before the delete) finally
    // resolves. A stale reload with no generation guard would overwrite the
    // now-rendered base tree with this stale local-branch content.
    resolveOldTree({ data: { data: { branch: 'test-branch', commit: 'c1', nodes: LOCAL_NODES } } })
    await flushPromises()

    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(false)
    labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('base.md')
    expect(labels).not.toContain('feature.md')
  })

  it('final: a create broadcast whose follow-up catalog GET fails still shows the new branch', async () => {
    // The create mirror of rev3's delete filter: the branch already exists
    // server-side when Branch Manager dispatches, so a failing follow-up
    // catalog GET must not leave it missing from the selector.
    routeGets({ catalog: { ok: true, base_branch: 'main', default_merge_target: null, branches: [
      { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    ] } })
    const wrapper = await mountExplorer()
    expect(wrapper.find('.fx-group-select').exists()).toBe(false)

    routeGets({ catalogFails: true })
    window.dispatchEvent(new CustomEvent('fg:git_branches_changed', {
      detail: { project: 'p', action: 'create', branch: 'test-branch' },
    }))
    await flushPromises()

    const optionValues = wrapper.find('.fx-group-select').findAll('option')
      .map((o) => (o.element as HTMLOptionElement).value)
    expect(optionValues).toContain('local:test-branch')

    // And the placeholder entry is a working option: selecting it reads the tree.
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()
    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(true)
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('feature.md')
  })

  it('final: a reload for project A still awaiting its tree does not overwrite project B after a switch', async () => {
    // reload() and the project-switch watcher used separate generation counters,
    // so neither invalidated the other: a refresh for A resolving after the
    // switch to B had rendered B put A's tree on B's screen.
    const A_NODES = [
      f({ id: 'a1', parent_id: null, type: 'file', name: 'a-only.md', label: 'a-only.md', path: 'a-only.md' }),
    ]
    const B_NODES = [
      f({ id: 'x1', parent_id: null, type: 'file', name: 'b-only.md', label: 'b-only.md', path: 'b-only.md' }),
    ]
    let resolveOldA!: (v: unknown) => void
    const oldAPromise = new Promise((resolve) => { resolveOldA = resolve })
    let aTreeCalls = 0
    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) return Promise.resolve({ data: { status: { slots: [] } } })
      if (url.includes('/git/branches')) return Promise.resolve({ data: NO_GROUP_CATALOG })
      if (url.includes('/projects/project-a/files/tree')) {
        aTreeCalls += 1
        // #1: mount's first load -- resolves. #2: the manual refresh -- held open.
        return aTreeCalls === 1 ? Promise.resolve({ data: { data: { nodes: A_NODES } } }) : oldAPromise
      }
      return Promise.resolve({ data: { data: { nodes: B_NODES } } })
    })
    apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })

    useLayoutStore().setFileExplorerCollapsed(false)
    const wrapper = mount(FileExplorer, {
      props: { projectId: 'project-a' },
      global: { plugins: [i18n] },
    })
    mountedWrappers.push(wrapper)
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('a-only.md')

    await wrapper.findAll('.sdb-act-btn')[0].trigger('click')
    await flushPromises()
    expect(aTreeCalls).toBe(2)

    await wrapper.setProps({ projectId: 'project-b' })
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('b-only.md')

    resolveOldA({ data: { data: { nodes: A_NODES } } })
    await flushPromises()

    const labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('b-only.md')
    expect(labels).not.toContain('a-only.md')
    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
    expect(wrapper.find('[data-test="file-explorer-refresh-error"]').exists()).toBe(false)
  })

  it('final: a branch picked while the first load is still fetching the base tree is not overwritten by that late base tree', async () => {
    // Same counter split, other direction: the mount watcher's base-tree fetch
    // resolving after the user already picked a local branch replaced the
    // branch tree with base while the selector/badge still showed the branch.
    let resolveFirstBase!: (v: unknown) => void
    const firstBasePromise = new Promise((resolve) => { resolveFirstBase = resolve })
    let baseCalls = 0
    getRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/status')) return Promise.resolve({ data: { status: { slots: [] } } })
      if (url.includes('/git/branches/tree')) {
        return Promise.resolve({ data: { data: { branch: 'test-branch', commit: 'c1', nodes: LOCAL_NODES } } })
      }
      if (url.includes('/git/branches')) return Promise.resolve({ data: NO_GROUP_CATALOG })
      baseCalls += 1
      return baseCalls === 1 ? firstBasePromise : Promise.resolve({ data: { data: { nodes: BASE_NODES } } })
    })
    apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })

    const wrapper = await mountExplorer() // catalog in, base tree still pending
    expect(baseCalls).toBe(1)
    await wrapper.get('.fx-group-select').setValue('local:test-branch')
    await flushPromises()
    expect(wrapper.findAll('.tree-lbl').map((n) => n.text())).toContain('feature.md')

    resolveFirstBase({ data: { data: { nodes: BASE_NODES } } })
    await flushPromises()

    expect(wrapper.find('[data-test="local-branch-readonly-badge"]').exists()).toBe(true)
    const labels = wrapper.findAll('.tree-lbl').map((n) => n.text())
    expect(labels).toContain('feature.md')
    expect(labels).not.toContain('base.md')
  })
})
