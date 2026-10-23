/**
 * flowgate.v02.0023 T0006 — project group tree in features/project (api + store).
 *
 * The tree request path and its cache live in the project store so the Designer Project
 * Explorer and File Explorer (0024) share one source instead of each owning a tree store.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const getRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { fetchProjectGroupTree } from '@/features/project/api'
import { useProjectStore } from '@/features/project/store'

const RAW = [
  { id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha', type_code: null, has_md: false },
  { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' },
  { id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer', number: '0001' },
  { id: 'p.ui.0001.0001-R', parent_id: 'p.ui.0001', node_type: 'document', label: 'Spec' },
  { id: 'x', parent_id: 'project:p', node_type: 'something-new', label: 'Unknown' },
]

const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  getRequest.mockReset()
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('fetchProjectGroupTree (api)', () => {
  it('requests the flat tree and normalizes it, skipping unknown node kinds', async () => {
    getRequest.mockResolvedValue(ok(RAW))
    const nodes = await fetchProjectGroupTree('p', { includeTerminal: false, force: true })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/groups/tree', { include_terminal: 'false', force: 'true' })
    expect(nodes).toEqual([
      { id: 'project:p', parentId: null, kind: 'project', label: 'Alpha', title: null, number: null },
      { id: 'module:p:ui', parentId: 'project:p', kind: 'module', label: 'ui', title: 'UI 개선', number: null },
      { id: 'p.ui.0001', parentId: 'module:p:ui', kind: 'group', label: 'Designer', title: null, number: '0001' },
      { id: 'p.ui.0001.0001-R', parentId: 'p.ui.0001', kind: 'document', label: 'Spec', title: null, number: null },
    ])
  })

  // flowgate.v02.0088 F10: the Error carries only the server's own words; without them it is empty and the screen
  // shows its localized load_failed instead of an English default from the adapter.
  it('throws a plain Error on failure, empty when the server said nothing', async () => {
    getRequest.mockRejectedValue(new Error('boom'))
    const error = await fetchProjectGroupTree('p', { includeTerminal: true, force: false }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(Error)
    expect((error as Error).message).toBe('')
  })
})

describe('useProjectStore().fetchGroupTree', () => {
  it('caches per project and variant and answers later reads from the cache', async () => {
    const store = useProjectStore()
    getRequest.mockResolvedValueOnce(ok(RAW)).mockResolvedValueOnce(ok(RAW.slice(0, 1)))
    const pruned = await store.fetchGroupTree('p')
    expect(await store.fetchGroupTree('p')).toBe(pruned)
    expect(store.getCachedGroupTree('p')).toBe(pruned)
    expect(getRequest).toHaveBeenCalledTimes(1)

    // The include_terminal variant is a different list and never answers for the pruned one.
    const full = await store.fetchGroupTree('p', { includeTerminal: true })
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(getRequest.mock.calls[1][1]).toEqual({ include_terminal: 'true', force: 'false' })
    expect(full).toHaveLength(1)
    expect(store.getCachedGroupTree('p')).toBe(pruned)
  })

  it('force bypasses the cache and replaces it', async () => {
    const store = useProjectStore()
    getRequest.mockResolvedValueOnce(ok(RAW)).mockResolvedValueOnce(ok(RAW.slice(0, 2)))
    await store.fetchGroupTree('p')
    const fresh = await store.fetchGroupTree('p', { force: true })
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(fresh).toHaveLength(2)
    expect(store.getCachedGroupTree('p')).toBe(fresh)
  })

  it('joins an in-flight read; a forced read does not join a non-forced one', async () => {
    const store = useProjectStore()
    const first = deferred<unknown>()
    getRequest.mockReturnValueOnce(first.promise).mockResolvedValueOnce(ok(RAW.slice(0, 2)))
    const a = store.fetchGroupTree('p')
    const b = store.fetchGroupTree('p')
    expect(getRequest).toHaveBeenCalledTimes(1)
    const forced = store.fetchGroupTree('p', { force: true })
    expect(getRequest).toHaveBeenCalledTimes(2)
    first.resolve(ok(RAW))
    expect(await a).toBe(await b)
    expect(await forced).toHaveLength(2)
  })

  it('a stale response that finishes last never overwrites a fresher cached tree', async () => {
    const store = useProjectStore()
    const slow = deferred<unknown>()
    getRequest.mockReturnValueOnce(slow.promise).mockResolvedValueOnce(ok(RAW.slice(0, 2)))
    const older = store.fetchGroupTree('p')
    const newer = await store.fetchGroupTree('p', { force: true })
    slow.resolve(ok(RAW))
    // The older caller still gets its own answer…
    expect(await older).toHaveLength(4)
    // …but the cache keeps the newer tree.
    expect(store.getCachedGroupTree('p')).toBe(newer)
  })

  it('keeps the last good tree when a read fails', async () => {
    const store = useProjectStore()
    getRequest.mockResolvedValueOnce(ok(RAW)).mockRejectedValueOnce(new Error('down'))
    const good = await store.fetchGroupTree('p')
    await expect(store.fetchGroupTree('p', { force: true })).rejects.toThrow()
    expect(store.getCachedGroupTree('p')).toBe(good)
  })

  it('exposes the current project id without needing the project list', () => {
    const store = useProjectStore()
    expect(store.currentProjectId).toBeNull()
    store.setCurrentProject({
      id: 'p', name: 'P', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [],
    })
    expect(store.currentProjectId).toBe('p')
    expect(store.currentProject).toBeNull()
  })
})
