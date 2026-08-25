/**
 * 0454 T0006 §2 — the group-tree cache and inflight registry are split per display variant.
 *
 * The server now answers two different payloads for the same project+branch:
 * `include_terminal=true` (everything) and `include_terminal=false` (final-approved and
 * discarded groups, plus their descendants, pruned away). The store therefore has to key both
 * the completed cache AND the inflight registry on that flag: if it did not, the sidebar's
 * default hidden load would happily hand its pruned array to DocHeader / dashboard navigation
 * / the creation modals — every one of which needs the full tree — or a "show completed"
 * toggle would join an in-flight hidden request and paint a tree that has none of the groups
 * the user just asked to see.
 *
 * The 0449 T0004 joining contract is unchanged WITHIN a variant, and is re-pinned here so the
 * split cannot be implemented by simply giving every call its own request.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useExplorerStore } from '@main/stores/explorer'
import { useProjectStore } from '@main/stores/project'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest }))

const node = (id: string, parent_id: string | null, node_type: string, extra: object = {}) => ({
  id, parent_id, node_type, type_code: null, number: null, filename: null,
  label: id, has_md: false, md_path: null, ...extra,
})

const PROJECT = node('project:p1', null, 'project')
const MODULE = node('module:p1:default', 'project:p1', 'module')
const DONE_GROUP = node('p1.default.0001', 'module:p1:default', 'group', { is_final_approved: true, is_discarded: false })
const DONE_DOC = node('p1.default.0001.0001-R', 'p1.default.0001', 'document', { type_code: 'R' })
const OPEN_GROUP = node('p1.default.0002', 'module:p1:default', 'group', { is_final_approved: false, is_discarded: false })
const OPEN_DOC = node('p1.default.0002.0001-R', 'p1.default.0002', 'document', { type_code: 'R' })

const FULL_NODES = [PROJECT, MODULE, DONE_GROUP, DONE_DOC, OPEN_GROUP, OPEN_DOC]
const PRUNED_NODES = [PROJECT, MODULE, OPEN_GROUP, OPEN_DOC]

const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })
const isFullUrl = (url: string) => /[?&]include_terminal=true(&|$)/.test(url)
const timeout = () =>
  Object.assign(new Error('timeout of 130000ms exceeded'), { code: 'ECONNABORTED' })

function serveBothVariants() {
  getRequest.mockImplementation((url: string) =>
    Promise.resolve(ok(isFullUrl(String(url)) ? FULL_NODES : PRUNED_NODES)),
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const urls = () => getRequest.mock.calls.map((call) => String(call[0]))

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  localStorage.clear()
})

describe('explorer store — include_terminal is always spelled out in the URL', () => {
  it('the default is the full variant, and it says so', async () => {
    serveBothVariants()
    const store = useExplorerStore()

    await store.fetchGroupTree('p1')

    expect(urls()[0]).toContain('/api/v1/projects/p1/groups/tree')
    expect(urls()[0]).toContain('branch=main')
    expect(urls()[0]).toContain('include_terminal=true')
  })

  it('the hidden variant sends include_terminal=false', async () => {
    serveBothVariants()
    const store = useExplorerStore()

    const nodes = await store.fetchGroupTree('p1', true, false)

    expect(urls()[0]).toContain('include_terminal=false')
    // And it really is the pruned payload — the flag is not decoration.
    expect(nodes.map((n) => n.id)).toEqual(PRUNED_NODES.map((n) => n.id))
  })

  it('the long-timeout policy still applies with the extra query parameter', async () => {
    // apiTreeTimeout.spec.ts owns the policy itself; this just pins that the URL this store
    // builds is still the shape that policy matches (`/groups/tree`), query and all.
    serveBothVariants()
    const store = useExplorerStore()
    await store.fetchGroupTree('p1', true, false)
    expect(urls()[0]).toMatch(/\/groups\/tree\?/)
  })
})

describe('explorer store — the two variants never share a cached tree', () => {
  it('a completed full tree does not answer a pruned request, or the other way round', async () => {
    serveBothVariants()
    const store = useExplorerStore()

    const full = await store.fetchGroupTree('p1', false, true)
    const pruned = await store.fetchGroupTree('p1', false, false)

    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(full).not.toBe(pruned)
    expect(full.map((n) => n.id)).toContain('p1.default.0001')
    expect(pruned.map((n) => n.id)).not.toContain('p1.default.0001')
  })

  it('each variant still serves its OWN cache without a second GET', async () => {
    serveBothVariants()
    const store = useExplorerStore()

    const firstFull = await store.fetchGroupTree('p1', false, true)
    const firstPruned = await store.fetchGroupTree('p1', false, false)
    expect(getRequest).toHaveBeenCalledTimes(2)

    expect(await store.fetchGroupTree('p1', false, true)).toBe(firstFull)
    expect(await store.fetchGroupTree('p1', false, false)).toBe(firstPruned)
    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('getCachedGroupTree returns the variant asked for, and undefined for the other', async () => {
    serveBothVariants()
    const store = useExplorerStore()

    await store.fetchGroupTree('p1', true, false)

    expect(store.getCachedGroupTree('p1', false)?.map((n) => n.id)).toEqual(
      PRUNED_NODES.map((n) => n.id),
    )
    // The important half: a consumer that needs the whole tree must NOT be handed the
    // pruned one just because that is what the sidebar happened to load.
    expect(store.getCachedGroupTree('p1', true)).toBeUndefined()
    // The default stays the full variant for compatibility.
    expect(store.getCachedGroupTree('p1')).toBeUndefined()
  })

  it('branch and variant are independent parts of the key', async () => {
    serveBothVariants()
    const store = useExplorerStore()
    const projectStore = useProjectStore()

    projectStore.currentBranch = 'main'
    await store.fetchGroupTree('p1', false, true)
    await store.fetchGroupTree('p1', false, false)
    projectStore.currentBranch = 'feature-x'
    await store.fetchGroupTree('p1', false, true)
    await store.fetchGroupTree('p1', false, false)

    expect(getRequest).toHaveBeenCalledTimes(4)
    expect(Object.keys(store.groupTreeCache).sort()).toEqual([
      'p1:feature-x:full',
      'p1:feature-x:pruned',
      'p1:main:full',
      'p1:main:pruned',
    ])
  })
})

describe('explorer store — inflight requests do not cross variants', () => {
  it('a full and a pruned request in flight together stay two requests with two answers', async () => {
    const fullGate = deferred<unknown>()
    const prunedGate = deferred<unknown>()
    getRequest.mockImplementation((url: string) =>
      isFullUrl(String(url)) ? fullGate.promise : prunedGate.promise,
    )
    const store = useExplorerStore()

    const full = store.fetchGroupTree('p1', true, true)
    const pruned = store.fetchGroupTree('p1', true, false)

    // Asserted before either settles: not joining has to happen at call time.
    expect(getRequest).toHaveBeenCalledTimes(2)

    // Deliberately resolved in the "wrong" order — the pruned caller must not receive the
    // full payload just because it arrived first.
    fullGate.resolve(ok(FULL_NODES))
    prunedGate.resolve(ok(PRUNED_NODES))
    const [fullNodes, prunedNodes] = await Promise.all([full, pruned])

    expect(fullNodes.map((n) => n.id)).toContain('p1.default.0001')
    expect(prunedNodes.map((n) => n.id)).not.toContain('p1.default.0001')
    expect(fullNodes).not.toBe(prunedNodes)
  })

  it('two force fetches of the SAME variant still join one GET (0449 contract)', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true, false)
    const second = store.fetchGroupTree('p1', true, false)
    expect(getRequest).toHaveBeenCalledTimes(1)

    gate.resolve(ok(PRUNED_NODES))
    const [a, b] = await Promise.all([first, second])
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)   // the same array, not a second parse
  })

  it('a failing wave on one variant costs one GET plus ONE shared retry', async () => {
    getRequest.mockRejectedValue(timeout())
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true, false)
    const second = store.fetchGroupTree('p1', true, false)
    await expect(first).rejects.toBeTruthy()
    await expect(second).rejects.toBeTruthy()

    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(store.groupError).toBe('tree_load_failed')
    expect(store.loadingGroup).toBe(false)
  })

  it('a failed variant leaves nothing cached, and the other variant is unaffected', async () => {
    getRequest.mockImplementation((url: string) =>
      isFullUrl(String(url)) ? Promise.resolve(ok(FULL_NODES)) : Promise.reject(timeout()),
    )
    const store = useExplorerStore()

    await expect(store.fetchGroupTree('p1', true, false)).rejects.toBeTruthy()
    expect(store.getCachedGroupTree('p1', false)).toBeUndefined()

    const full = await store.fetchGroupTree('p1', true, true)
    expect(full.map((n) => n.id)).toContain('p1.default.0001')
    expect(store.getCachedGroupTree('p1', true)).toBe(full)
  })
})

describe('explorer store — invalidateProject clears every variant and branch', () => {
  it('drops full and pruned across all branches of the project, and nothing else', async () => {
    serveBothVariants()
    const store = useExplorerStore()
    const projectStore = useProjectStore()

    projectStore.currentBranch = 'main'
    await store.fetchGroupTree('p1', false, true)
    await store.fetchGroupTree('p1', false, false)
    projectStore.currentBranch = 'feature-x'
    await store.fetchGroupTree('p1', false, true)
    await store.fetchGroupTree('p1', false, false)
    // A second project, to prove the sweep is scoped.
    await store.fetchGroupTree('p2', false, true)
    await store.fetchGroupTree('p2', false, false)
    expect(Object.keys(store.groupTreeCache)).toHaveLength(6)

    store.invalidateProject('p1')

    expect(Object.keys(store.groupTreeCache).sort()).toEqual([
      'p2:feature-x:full',
      'p2:feature-x:pruned',
    ])
    // And the next read of either p1 variant really goes back to the server.
    projectStore.currentBranch = 'main'
    getRequest.mockClear()
    await store.fetchGroupTree('p1', false, true)
    await store.fetchGroupTree('p1', false, false)
    expect(getRequest).toHaveBeenCalledTimes(2)
  })
})

describe('explorer store — revealDocInGroupTree works on the full variant', () => {
  it('finds a document that only the full tree contains, even with a pruned tree cached', async () => {
    serveBothVariants()
    const store = useExplorerStore()

    // The sidebar's state: only the pruned variant is loaded, and the target is not in it.
    await store.fetchGroupTree('p1', true, false)
    expect(store.getCachedGroupTree('p1', false)?.some((n) => n.id === DONE_DOC.id)).toBe(false)
    getRequest.mockClear()

    const found = await store.revealDocInGroupTree('p1', DONE_DOC.id)

    expect(found?.id).toBe(DONE_DOC.id)
    // Every request it made asked for the full variant — a pruned lookup here would have
    // reported "document not found" for a document that exists and is merely hidden.
    expect(urls().length).toBeGreaterThan(0)
    expect(urls().every(isFullUrl)).toBe(true)
    expect(store.selectedGroupNodeId).toBe(DONE_DOC.id)
    expect(store.isGroupNodeExpanded('p1', DONE_GROUP.id)).toBe(true)
    expect(store.isGroupNodeExpanded('p1', MODULE.id)).toBe(true)
  })

  it('reuses an already-cached FULL tree without a request', async () => {
    serveBothVariants()
    const store = useExplorerStore()
    await store.fetchGroupTree('p1', true, true)
    getRequest.mockClear()

    const found = await store.revealDocInGroupTree('p1', DONE_DOC.id)

    expect(found?.id).toBe(DONE_DOC.id)
    expect(getRequest).not.toHaveBeenCalled()
  })
})
