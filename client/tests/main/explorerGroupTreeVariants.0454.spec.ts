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

// A document created by a write that happens AFTER FULL_NODES was already read off the server —
// used by the write-ordering tests below to tell a pre-write response from a post-write one.
const NEW_DOC = node('p1.default.0002.0002-R', 'p1.default.0002', 'document', { type_code: 'R' })
const FRESH_FULL_NODES = [...FULL_NODES, NEW_DOC]

const ok = (nodes: unknown[], overview_summary?: unknown) =>
  ({ data: { data: overview_summary ? { nodes, overview_summary } : { nodes } } })
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

// 0454 T0007 rev5 — `force` now also decides what a caller is willing to JOIN, not only what it
// reads from the completed cache: a `force=true` caller must never join an in-flight NON-forced
// request, because that request's own URL never carries `force=true` and so the server was free
// to hand it a fetch that predates a write this caller's caller already knows completed
// (DashboardView.vue's two reveal-after-create paths). See fetchGroupTree's docstring.
describe('explorer store — a force=true caller never joins a non-forced in-flight request', () => {
  it('force=true, while a non-forced fetch of the SAME variant is in flight, starts its own GET', async () => {
    const nonForcedGate = deferred<unknown>()
    getRequest.mockReturnValueOnce(nonForcedGate.promise)
    const store = useExplorerStore()

    // Non-forced caller (e.g. DocHeader) starts first and is still in flight.
    const nonForced = store.fetchGroupTree('p1', false, true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    // A force=true caller (e.g. DashboardView's reveal-after-create) arrives next. It must NOT
    // join the non-forced fetch above — it must issue its own request, with force=true in the URL.
    const forcedGate = deferred<unknown>()
    getRequest.mockReturnValueOnce(forcedGate.promise)
    const forced = store.fetchGroupTree('p1', true, true)
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(urls()[1]).toContain('force=true')
    expect(urls()[0]).toContain('force=false')

    nonForcedGate.resolve(ok(FULL_NODES))
    forcedGate.resolve(ok(FULL_NODES))
    await Promise.all([nonForced, forced])
  })

  it('a non-forced caller MAY join an already in-flight force=true fetch', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const store = useExplorerStore()

    const forced = store.fetchGroupTree('p1', true, true)
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(urls()[0]).toContain('force=true')

    // A plain (non-forced) caller arrives while the force=true fetch is still running — it may
    // share it; the restriction only runs the other direction.
    const nonForced = store.fetchGroupTree('p1', false, true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    gate.resolve(ok(FULL_NODES))
    const [a, b] = await Promise.all([forced, nonForced])
    expect(a).toBe(b)
  })

  it('two concurrent force=true calls for the SAME variant still join one GET when no write happened in between (0449 contract preserved)', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true, true)
    const second = store.fetchGroupTree('p1', true, true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    gate.resolve(ok(FULL_NODES))
    const [a, b] = await Promise.all([first, second])
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)
  })

  // 0454 T0007 rev6 (rev5 review finding 1) — the test above only holds because nothing the
  // client knows about changed between the two calls. GroupExplorer's own reload() (initial
  // load / SSE refresh / hide-toggle) is ALSO always force=true, so it can be the "first" call
  // here just as easily as another force=true caller — and if a write (e.g. DashboardView's
  // handleRequirementCreated creating a document) happens while that reload is still in flight,
  // the reveal-after-create call that follows must NOT join it: that in-flight fetch's GET was
  // dispatched before the write and may come back without the new document.
  it('a force=true caller does NOT join an in-flight force=true fetch that started before a known write', async () => {
    const preWriteGate = deferred<unknown>()
    getRequest.mockReturnValueOnce(preWriteGate.promise)
    const store = useExplorerStore()

    // e.g. GroupExplorer's reload(), already running when the write below happens.
    const preWrite = store.fetchGroupTree('p1', true, true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    // The write itself (document creation) — invalidateProject is what the store learns it
    // from; DashboardView's handlers now call it before their reveal fetch (rev6).
    store.invalidateProject('p1')

    // The reveal-after-create call. Must issue its own fresh GET, not join `preWrite` above.
    const postWriteGate = deferred<unknown>()
    getRequest.mockReturnValueOnce(postWriteGate.promise)
    const postWrite = store.fetchGroupTree('p1', true, true)
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(urls()[1]).toContain('force=true')

    preWriteGate.resolve(ok(FULL_NODES))
    postWriteGate.resolve(ok(FRESH_FULL_NODES))
    const [preWriteNodes, postWriteNodes] = await Promise.all([preWrite, postWrite])
    expect(preWriteNodes.map((n) => n.id)).not.toContain(NEW_DOC.id)
    expect(postWriteNodes.map((n) => n.id)).toContain(NEW_DOC.id)
  })

  it('a force=true caller MAY still join an in-flight force=true fetch that started after the last known write', async () => {
    const store = useExplorerStore()
    store.invalidateProject('p1') // no in-flight fetch exists yet — this just sets the baseline

    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const first = store.fetchGroupTree('p1', true, true) // starts AFTER the write above
    expect(getRequest).toHaveBeenCalledTimes(1)

    const second = store.fetchGroupTree('p1', true, true)
    expect(getRequest).toHaveBeenCalledTimes(1) // joined — `first` is new enough to trust

    gate.resolve(ok(FULL_NODES))
    const [a, b] = await Promise.all([first, second])
    expect(a).toBe(b)
  })

  it("every fetchGroupTree URL spells out force explicitly", async () => {
    serveBothVariants()
    const store = useExplorerStore()

    await store.fetchGroupTree('p1', false, true)
    await store.fetchGroupTree('p1', true, true)

    expect(urls()[0]).toContain('force=false')
    expect(urls()[1]).toContain('force=true')
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

// 0454 T0007 rev6 (rev5 review finding 2) — rev5's cache write was unconditional: whichever of
// two overlapping (non-joined) fetches for the same key happened to RESOLVE last simply
// overwrote groupTreeCache/groupOverviewSummaryCache, regardless of which one started (and
// therefore read the server) more recently. A request that started before a write but resolves
// after a request that started following that write must not be allowed to undo it.
describe('explorer store — a stale response cannot overwrite a fresher cache commit', () => {
  it('the later-started fetch wins the cache even if the earlier-started one resolves last', async () => {
    const staleGate = deferred<unknown>()
    getRequest.mockReturnValueOnce(staleGate.promise)
    const store = useExplorerStore()

    // Started before the write below (non-forced, e.g. DocHeader), still in flight.
    const stale = store.fetchGroupTree('p1', false, true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    // The write (document creation), then a force=true reveal fetch — it cannot join `stale`
    // (non-forced), so it starts its own, later GET.
    store.invalidateProject('p1')
    const freshGate = deferred<unknown>()
    getRequest.mockReturnValueOnce(freshGate.promise)
    const fresh = store.fetchGroupTree('p1', true, true)
    expect(getRequest).toHaveBeenCalledTimes(2)

    // The later-started (fresh) fetch resolves FIRST and commits the cache...
    const freshSummary = { total_documents: 6, working_groups: 2, type_distribution: [] }
    freshGate.resolve(ok(FRESH_FULL_NODES, freshSummary))
    const freshResult = await fresh
    expect(freshResult.map((n) => n.id)).toContain(NEW_DOC.id)
    // The winning response's OWN resolved value is the reactive cache entry it just wrote —
    // see fetchGroupTree's docstring on why a winner returns groupTreeCache.value[key] itself.
    expect(store.getCachedGroupTree('p1', true)).toBe(freshResult)
    const committedSummary = store.getCachedGroupOverviewSummary('p1')
    expect(committedSummary?.total_documents).toBe(freshSummary.total_documents)

    // ...then the earlier-started (stale) fetch resolves LAST. It must not overwrite the cache
    // with its pre-write result — but the caller who made that exact call still gets its own
    // (accurate, for what it asked) answer back.
    const staleSummary = { total_documents: 5, working_groups: 2, type_distribution: [] }
    staleGate.resolve(ok(FULL_NODES, staleSummary))
    const staleResult = await stale
    expect(staleResult.map((n) => n.id)).not.toContain(NEW_DOC.id)
    expect(store.getCachedGroupTree('p1', true)).toBe(freshResult)
    expect(store.getCachedGroupOverviewSummary('p1')).toBe(committedSummary)
  })
})

// 0454 T0007 rev7 (rev6 review) — the test above only covers a stale response losing to a
// FRESHER one that already committed. `order >= committedOrder` alone does not defend a slot
// nothing has committed to since the write (first load, or invalidateProject's own clear): that
// key reads as `committedOrder ?? 0`, so a lone stale response can satisfy the check on its own
// and refill the slot invalidateProject just emptied — even with no fresher fetch in flight, let
// alone committed, to have superseded it.
describe('explorer store — invalidateProject cannot be refilled by a fetch that predates it', () => {
  it('a lone in-flight fetch that resolves after invalidateProject does not populate the cache with pre-write data', async () => {
    const staleGate = deferred<unknown>()
    getRequest.mockReturnValueOnce(staleGate.promise)
    const store = useExplorerStore()

    // e.g. DocHeader's own reload(), already running when the write below happens. First load —
    // nothing has ever committed to this key, so groupTreeCommittedOrder has no entry for it.
    const stale = store.fetchGroupTree('p1', false, true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    // The write (document creation). Unlike the test above, no fresher fetch follows it here —
    // the reveal-after-create call has not fired yet, or failed outright.
    store.invalidateProject('p1')

    // The lone in-flight fetch, which started before the write, resolves now with pre-write
    // data. It must not populate the cache invalidateProject just emptied.
    staleGate.resolve(ok(FULL_NODES, { total_documents: 5, working_groups: 2, type_distribution: [] }))
    const staleResult = await stale
    // The caller that made this exact call still gets its own (accurate, for what it asked)
    // answer back...
    expect(staleResult).toEqual(FULL_NODES)
    // ...but the shared cache must stay empty rather than silently reacquiring stale data.
    expect(store.getCachedGroupTree('p1', true)).toBeUndefined()
    expect(store.getCachedGroupOverviewSummary('p1')).toBeUndefined()
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

// 0454 T0007 — the MainPanel overview cards (총 문서 수 / 진행 중 / 타입 분포) went through several
// designs: warming the FULL group tree on every default-screen entry (rev0: two tree payloads
// on the wire for one screen), a dedicated `fetchGroupOverviewSummary` endpoint that reran
// `process_service.get_group_tree` a second time per page and never refetched on an ordinary
// refresh (rev1), riding inside the SAME `/groups/tree` response UNCONDITIONALLY (rev2 — which
// fixed rev1's two problems but changed what `/groups/tree` returns to every pre-existing
// caller, breaking T0006 §1.1), and a standalone `fetchGroupOverviewSummary` hitting its own
// route again, called by MainPanel.vue on its own triggers (rev3) — which the rev4 review found
// left two INDEPENDENT, not-guaranteed-to-overlap requests per screen refresh, costing a second
// full `get_group_tree` DB call whenever they landed sequentially instead of concurrently.
//
// rev5: `fetchGroupOverviewSummary` and its route are GONE. Every `fetchGroupTree` call now asks
// the server for `overview_summary` (an opt-in `include_summary=true` query flag — see
// tree_routes.get_groups_tree's docstring) and, when the response carries it, writes it into
// groupOverviewSummaryCache as a side effect. There is no separate request left for MainPanel to
// issue or to race against GroupExplorer's — the sidebar's own tree fetch IS the request that
// keeps the cards current.
const nodesAndSummary = (nodes: unknown[], summary: object) => ({ data: { data: { nodes, overview_summary: summary } } })

describe('explorer store — overview_summary rides fetchGroupTree, not a separate request (0454 T0007 rev5)', () => {
  const SUMMARY = { total_documents: 3, working_groups: 1, type_distribution: [{ type: 'T', count: 3 }] }

  it('every fetchGroupTree URL asks for include_summary=true', async () => {
    serveBothVariants()
    const store = useExplorerStore()

    await store.fetchGroupTree('p1', true, true)
    await store.fetchGroupTree('p1', true, false)

    expect(urls()).toHaveLength(2)
    expect(urls().every((u) => u.includes('include_summary=true'))).toBe(true)
  })

  it('a response carrying overview_summary populates the cache, from ONE request', async () => {
    getRequest.mockResolvedValue(nodesAndSummary(PRUNED_NODES, SUMMARY))
    const store = useExplorerStore()

    expect(store.getCachedGroupOverviewSummary('p1')).toBeUndefined()
    const nodes = await store.fetchGroupTree('p1', true, false)

    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(nodes.map((n) => n.id)).toEqual(PRUNED_NODES.map((n) => n.id))
    expect(store.getCachedGroupOverviewSummary('p1')).toEqual(SUMMARY)
  })

  it('either variant refreshes the same project-wide summary cache', async () => {
    const store = useExplorerStore()
    const SUMMARY_2 = { total_documents: 9, working_groups: 4, type_distribution: [{ type: 'R', count: 9 }] }

    getRequest.mockResolvedValueOnce(nodesAndSummary(PRUNED_NODES, SUMMARY))
    await store.fetchGroupTree('p1', true, false)
    expect(store.getCachedGroupOverviewSummary('p1')).toEqual(SUMMARY)

    getRequest.mockResolvedValueOnce(nodesAndSummary(FULL_NODES, SUMMARY_2))
    await store.fetchGroupTree('p1', true, true)
    expect(store.getCachedGroupOverviewSummary('p1')).toEqual(SUMMARY_2)
  })

  it('a response that omits overview_summary leaves the existing cache alone', async () => {
    const store = useExplorerStore()
    store.groupOverviewSummaryCache['p1:main'] = SUMMARY
    getRequest.mockResolvedValue(ok(PRUNED_NODES)) // no overview_summary key at all

    await store.fetchGroupTree('p1', true, false)

    expect(store.getCachedGroupOverviewSummary('p1')).toEqual(SUMMARY)
  })

  it('invalidateProject clears the cached summary, and the next fetchGroupTree restores it', async () => {
    getRequest.mockResolvedValue(nodesAndSummary(PRUNED_NODES, SUMMARY))
    const store = useExplorerStore()

    await store.fetchGroupTree('p1', true, false)
    expect(store.getCachedGroupOverviewSummary('p1')).toEqual(SUMMARY)

    store.invalidateProject('p1')
    expect(store.getCachedGroupOverviewSummary('p1')).toBeUndefined()

    await store.fetchGroupTree('p1', true, false)
    expect(store.getCachedGroupOverviewSummary('p1')).toEqual(SUMMARY)
  })

  it('a failed fetch leaves the existing summary cache alone (fetchGroupTree still rejects)', async () => {
    const store = useExplorerStore()
    store.groupOverviewSummaryCache['p1:main'] = SUMMARY
    getRequest.mockRejectedValue(timeout())

    await expect(store.fetchGroupTree('p1', true, false)).rejects.toBeTruthy()

    expect(store.getCachedGroupOverviewSummary('p1')).toEqual(SUMMARY)
  })

  it('fetchGroupOverviewSummary and the separate route no longer exist on the store', () => {
    const store = useExplorerStore()
    expect((store as Record<string, unknown>).fetchGroupOverviewSummary).toBeUndefined()
  })
})
