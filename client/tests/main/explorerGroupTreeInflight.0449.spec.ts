/**
 * 0449 T0004 item 2 — concurrent group-tree fetches share ONE request.
 *
 * NR0003 measured the incident shape on the live project: a reject and the re-approval that
 * follows it fire two SSE refreshes seconds apart, outside the 250 ms coalescing window, so
 * they stayed two logical reloads. `fetchGroupTree(force=true)` had no inflight registry, so
 * each reload owned its own first GET *and* its own retry GET — four multi-MB requests for
 * one reopen (probe: "첫 파동 2 + 재시도 파동 2 … 총 4 GET").
 *
 * `force` now means only "bypass the completed cache". Same project + branch already in
 * flight → join it, share its retry, and receive the same value or the same error.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useExplorerStore } from '@main/stores/explorer'
import { useProjectStore } from '@main/stores/project'
import { buildLoadNodes } from './fixtures/groupTreeLoadShape'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest }))

const groupTreeOk = {
  data: { data: { nodes: [{ id: 'g1', parent_id: null, node_type: 'group' }] } },
}

/**
 * 0449 TR0005 rev1 — the load payload, not a one-node stand-in.
 *
 * rev0's whole file answered on a single node. Joining is what completion criterion 3 asks
 * for **on the 4.8 MB shape**, and a one-node payload cannot show the thing that matters
 * there: that the joined caller gets the SAME array instance rather than a second parse of a
 * multi-megabyte body. It is built once at module load — 5,883 nodes, 1.25 MB serialized, the
 * same node counts as the server-side fixture in
 * test_explorer_tree_and_return_point_0449.py, which measures that shape at 4.86 MB under the
 * pre-0449 nesting — and reused.
 */
// buildLoadNodes() now lives in ./fixtures/groupTreeLoadShape (0454 T0004 §Vitest 회귀
// 검증 item 1) — called with no options it reproduces this exact 5,883-node shape.
const LOAD_NODES = buildLoadNodes()
const groupTreeLoadOk = { data: { data: { nodes: LOAD_NODES } } }
const timeout = () =>
  Object.assign(new Error('timeout of 130000ms exceeded'), { code: 'ECONNABORTED' })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  localStorage.clear()
})

describe('explorer store — concurrent forced group-tree fetches join one request', () => {
  it('two overlapping force fetches issue a single GET and resolve to the same nodes', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true)
    const second = store.fetchGroupTree('p1', true)

    // Asserted BEFORE the request settles: joining has to happen at call time, not after.
    expect(getRequest).toHaveBeenCalledTimes(1)

    gate.resolve(groupTreeOk)
    const [a, b] = await Promise.all([first, second])
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)
    expect(a).toHaveLength(1)
  })

  it('the SSE shape: a second refresh ~250ms into the first one still joins it', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true)
    await sleep(250)
    const second = store.fetchGroupTree('p1', true)

    expect(getRequest).toHaveBeenCalledTimes(1)
    gate.resolve(groupTreeOk)
    await Promise.all([first, second])
    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  it('a failing wave costs one GET plus ONE shared retry, not two of each', async () => {
    getRequest.mockRejectedValue(timeout())
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true)
    const second = store.fetchGroupTree('p1', true)

    await expect(first).rejects.toBeTruthy()
    await expect(second).rejects.toBeTruthy()
    // 2 = the first attempt + getTreeWithRetry's single retry, shared by both callers.
    // Before the registry this was 4.
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(store.groupError).toBe('tree_load_failed')
    expect(store.loadingGroup).toBe(false)
  })

  it('joined callers all receive the SAME rejection', async () => {
    const error = timeout()
    getRequest.mockRejectedValue(error)
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true)
    const second = store.fetchGroupTree('p1', true)

    await expect(first).rejects.toBe(error)
    await expect(second).rejects.toBe(error)
  })

  it('the registry is cleared on success, so a later refresh is a fresh request', async () => {
    getRequest.mockResolvedValue(groupTreeOk)
    const store = useExplorerStore()

    await store.fetchGroupTree('p1', true)
    await store.fetchGroupTree('p1', true)

    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('the registry is cleared on failure too, so a retry really retries', async () => {
    getRequest.mockRejectedValue(timeout())
    const store = useExplorerStore()
    await expect(store.fetchGroupTree('p1', true)).rejects.toBeTruthy()
    expect(getRequest).toHaveBeenCalledTimes(2)

    getRequest.mockReset()
    getRequest.mockResolvedValue(groupTreeOk)
    expect(await store.fetchGroupTree('p1', true)).toHaveLength(1)
    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  it('does NOT join requests for a different branch', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValue(gate.promise)
    const store = useExplorerStore()
    const projectStore = useProjectStore()

    projectStore.currentBranch = 'main'
    const onMain = store.fetchGroupTree('p1', true)
    projectStore.currentBranch = 'feature-x'
    const onFeature = store.fetchGroupTree('p1', true)

    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(getRequest.mock.calls[0][0]).toContain('branch=main')
    expect(getRequest.mock.calls[1][0]).toContain('branch=feature-x')

    gate.resolve(groupTreeOk)
    await Promise.all([onMain, onFeature])
  })

  it('does NOT join requests for a different project', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValue(gate.promise)
    const store = useExplorerStore()

    const p1 = store.fetchGroupTree('p1', true)
    const p2 = store.fetchGroupTree('p2', true)

    expect(getRequest).toHaveBeenCalledTimes(2)
    gate.resolve(groupTreeOk)
    await Promise.all([p1, p2])
  })
})

describe('explorer store — joining on the load-scale payload (criterion 3)', () => {
  it('the fixture really is the load shape', () => {
    // Guards the guard, the same way the server fixture does. 5,883 nodes / 1.25 MB flat here;
    // the server test measures the same node count at 4.86 MB under the pre-0449 nested shape,
    // and asserts that figure there rather than restating it from this side.
    expect(LOAD_NODES).toHaveLength(1 + 2 + 2 * 84 + 2 * 84 * 34)
    expect(JSON.stringify(groupTreeLoadOk).length).toBeGreaterThan(1_200_000)
  })

  it('two overlapping refreshes of the load tree cost ONE GET and one parse', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true)
    const second = store.fetchGroupTree('p1', true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    gate.resolve(groupTreeLoadOk)
    const [a, b] = await Promise.all([first, second])

    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(a).toHaveLength(LOAD_NODES.length)
    // The SAME array, not an equal copy: the joined caller never re-walked 5,883 nodes.
    expect(a).toBe(b)
  })

  it('the SSE shape at load size: a refresh 250ms in still joins', async () => {
    const gate = deferred<unknown>()
    getRequest.mockReturnValueOnce(gate.promise)
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true)
    await sleep(250)
    const second = store.fetchGroupTree('p1', true)
    expect(getRequest).toHaveBeenCalledTimes(1)

    gate.resolve(groupTreeLoadOk)
    const [a, b] = await Promise.all([first, second])
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)
  })

  it('a failing load-size wave costs one GET plus ONE shared retry, not two of each', async () => {
    getRequest.mockRejectedValue(timeout())
    const store = useExplorerStore()

    const first = store.fetchGroupTree('p1', true)
    const second = store.fetchGroupTree('p1', true)
    await expect(first).rejects.toBeTruthy()
    await expect(second).rejects.toBeTruthy()

    // This is the number NR0003 measured as four multi-MB GETs for one reopen.
    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('the UI hierarchy and the terminal flags survive the joined load payload', async () => {
    // Criterion 3's other half, on the payload the client actually keeps: parent_id still
    // reconstructs the tree GroupTreeNode renders, and the toggle's flags are intact.
    getRequest.mockResolvedValue(groupTreeLoadOk)
    const store = useExplorerStore()

    const nodes = await store.fetchGroupTree('p1', true) as Array<any>

    const byId = new Map(nodes.map((n) => [n.id, n]))
    expect(byId.size).toBe(nodes.length)               // every id exactly once
    expect(nodes.some((n) => 'children' in n)).toBe(false)
    expect(nodes.filter((n) => n.parent_id === null)).toHaveLength(1)
    for (const node of nodes) {
      if (node.parent_id !== null) expect(byId.has(node.parent_id)).toBe(true)
    }
    const groups = nodes.filter((n) => n.node_type === 'group')
    expect(groups).toHaveLength(2 * 84)
    expect(groups.every((g) => 'is_final_approved' in g && 'is_discarded' in g)).toBe(true)
  })
})
