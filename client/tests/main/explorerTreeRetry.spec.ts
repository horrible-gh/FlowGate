/**
 * 0283 TS0006 — explorer tree fetches retry once before surfacing tree_load_failed (TR0005 §C).
 *
 * Bug 0283.0001-B: a single transient tree fetch failure (a client timeout on a slow
 * remote-storage directory walk, or a momentary 5xx) put "트리를 불러오지 못했습니다."
 * on screen and forced a manual page reload. `getTreeWithRetry` now retries once after a
 * short backoff, so a one-off blip self-heals while a persistent failure keeps the old UX.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useExplorerStore } from '@main/stores/explorer'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest }))

const fileNode = {
  id: '1',
  parent_id: null,
  type: 'file',
  name: 'a.md',
  label: 'a.md',
  path: 'a.md',
  permissions: ['read'],
}

const fileTreeOk = { data: { data: { nodes: [fileNode] } } }
const fileTreePartial = { data: { data: { nodes: [], complete: false, scan_failures: [{ path: 'docs', reason: 'scan_failed' }] } } }
const groupTreeOk = { data: { data: { nodes: [{ id: '1', parent_id: null, node_type: 'group' }] } } }
const branchTreeOk = {
  data: { data: { branch: 'main', commit: 'abc1234', nodes: [fileNode] } },
}

const timeout = () => Object.assign(new Error('timeout of 130000ms exceeded'), { code: 'ECONNABORTED' })

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  localStorage.clear()
})

describe('explorer store — transient tree failures self-heal', () => {
  it('retries the file tree once and returns the nodes from the second attempt', async () => {
    getRequest.mockRejectedValueOnce(timeout()).mockResolvedValueOnce(fileTreeOk)
    const store = useExplorerStore()

    const nodes = await store.fetchFileTree('p1')

    expect(nodes).toHaveLength(1)
    expect(getRequest).toHaveBeenCalledTimes(2)
    // Same URL both times — the retry re-issues the request, it does not fall back elsewhere.
    expect(getRequest.mock.calls[0][0]).toBe(getRequest.mock.calls[1][0])
    expect(getRequest.mock.calls[0][0]).toContain('/files/tree')
    expect(store.fileError).toBeNull()
    expect(store.loadingFile).toBe(false)
  })

  it('retries the group tree once and returns the nodes from the second attempt', async () => {
    getRequest.mockRejectedValueOnce(timeout()).mockResolvedValueOnce(groupTreeOk)
    const store = useExplorerStore()

    const nodes = await store.fetchGroupTree('p1')

    expect(nodes).toHaveLength(1)
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(getRequest.mock.calls[1][0]).toContain('/groups/tree')
    expect(store.groupError).toBeNull()
  })

  it('retries the group-branch tree once and returns the commit-pinned nodes', async () => {
    getRequest.mockRejectedValueOnce(timeout()).mockResolvedValueOnce(branchTreeOk)
    const store = useExplorerStore()

    const res = await store.fetchGroupBranchTree('p1', 'flowgate.default.0283')

    expect(res.commit).toBe('abc1234')
    expect(res.nodes).toHaveLength(1)
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(getRequest.mock.calls[1][0]).toContain('/git/groups/')
    expect(store.fileError).toBeNull()
  })

  it('does not double-fetch when the first attempt succeeds', async () => {
    getRequest.mockResolvedValueOnce(fileTreeOk)
    const store = useExplorerStore()

    await store.fetchFileTree('p1')

    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  it('keeps a completed cache when a forced refresh is explicitly incomplete', async () => {
    vi.useFakeTimers()
    try {
      getRequest.mockResolvedValueOnce(fileTreeOk)
        .mockResolvedValueOnce(fileTreePartial)
        .mockResolvedValueOnce(fileTreePartial)
      const store = useExplorerStore()

      const completeNodes = await store.fetchFileTree('p1')
      const displayedNodes = await store.fetchFileTree('p1', true)
      await vi.advanceTimersByTimeAsync(1500)

      expect(displayedNodes).toEqual(completeNodes)
      expect(store.fileTreeCache['p1:main']).toEqual(completeNodes)
      expect(store.isFileTreeDegraded('p1')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows an initial incomplete tree without caching it, then recovers in background', async () => {
    vi.useFakeTimers()
    try {
      getRequest.mockResolvedValueOnce(fileTreePartial).mockResolvedValueOnce(fileTreeOk)
      const store = useExplorerStore()

      const visible = await store.fetchFileTree('p1')
      expect(visible).toEqual([])
      expect(store.fileTreeCache['p1:main']).toBeUndefined()
      expect(store.isFileTreeDegraded('p1')).toBe(true)

      await vi.advanceTimersByTimeAsync(1500)
      expect(visible).toEqual([fileNode])
      expect(getRequest).toHaveBeenCalledTimes(2)
      expect(store.isFileTreeDegraded('p1')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps degraded state scoped to the cached project tree', async () => {
    vi.useFakeTimers()
    try {
      getRequest.mockResolvedValueOnce(fileTreeOk)
        .mockResolvedValueOnce(fileTreePartial)
        .mockResolvedValueOnce(fileTreePartial)
      const store = useExplorerStore()

      await store.fetchFileTree('p2')
      await store.fetchFileTree('p1')
      expect(store.isFileTreeDegraded('p1')).toBe(true)

      await store.fetchFileTree('p2')
      await vi.advanceTimersByTimeAsync(1500)
      expect(getRequest).toHaveBeenCalledTimes(3)
      expect(store.isFileTreeDegraded('p1')).toBe(true)
      expect(store.isFileTreeDegraded('p2')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('still surfaces tree_load_failed when both attempts fail', async () => {
    getRequest.mockRejectedValue(timeout())
    const store = useExplorerStore()

    await expect(store.fetchFileTree('p1')).rejects.toBeTruthy()

    expect(getRequest).toHaveBeenCalledTimes(2) // retry is bounded — exactly one extra try
    expect(store.fileError).toBe('tree_load_failed')
    expect(store.loadingFile).toBe(false)
  })

  it('still surfaces tree_load_failed for the group tree when both attempts fail', async () => {
    getRequest.mockRejectedValue(timeout())
    const store = useExplorerStore()

    await expect(store.fetchGroupTree('p1')).rejects.toBeTruthy()

    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(store.groupError).toBe('tree_load_failed')
    expect(store.loadingGroup).toBe(false)
  })

  it('does not cache a failed tree, so a later load can still succeed', async () => {
    getRequest.mockRejectedValue(timeout())
    const store = useExplorerStore()
    await expect(store.fetchFileTree('p1')).rejects.toBeTruthy()

    getRequest.mockReset()
    getRequest.mockResolvedValueOnce(fileTreeOk)
    expect(await store.fetchFileTree('p1')).toHaveLength(1)
    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  it('recovers a partial refresh once and updates the visible tree and cache', async () => {
    vi.useFakeTimers()
    try {
      getRequest.mockResolvedValueOnce(fileTreePartial).mockResolvedValueOnce(fileTreeOk)
      const store = useExplorerStore()
      const visible = await store.fetchFileTree('p1')

      expect(store.isFileTreeDegraded('p1')).toBe(true)
      await vi.advanceTimersByTimeAsync(1500)

      expect(getRequest).toHaveBeenCalledTimes(2)
      expect(visible).toEqual([fileNode])
      expect(store.fileTreeCache['p1:main']).toEqual([fileNode])
      expect(store.isFileTreeDegraded('p1')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops after one partial recovery response and preserves the complete cache', async () => {
    vi.useFakeTimers()
    try {
      getRequest.mockResolvedValueOnce(fileTreeOk)
        .mockResolvedValueOnce(fileTreePartial)
        .mockResolvedValueOnce(fileTreePartial)
      const store = useExplorerStore()
      const complete = await store.fetchFileTree('p1')
      const visible = await store.fetchFileTree('p1', true)
      await vi.advanceTimersByTimeAsync(10000)

      expect(getRequest).toHaveBeenCalledTimes(3)
      expect(visible).toEqual(complete)
      expect(store.fileTreeCache['p1:main']).toEqual(complete)
      expect(store.isFileTreeDegraded('p1')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the partial view degraded when the single recovery request throws', async () => {
    vi.useFakeTimers()
    try {
      getRequest.mockResolvedValueOnce(fileTreePartial).mockRejectedValueOnce(timeout())
      const store = useExplorerStore()
      const visible = await store.fetchFileTree('p1')
      await vi.advanceTimersByTimeAsync(10000)

      expect(getRequest).toHaveBeenCalledTimes(2)
      expect(visible).toEqual([])
      expect(store.fileTreeCache['p1:main']).toBeUndefined()
      expect(store.isFileTreeDegraded('p1')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('coalesces a scheduled recovery behind a newer manual complete refresh', async () => {
    vi.useFakeTimers()
    try {
      getRequest.mockResolvedValueOnce(fileTreePartial).mockResolvedValueOnce(fileTreeOk)
      const store = useExplorerStore()
      const visible = await store.fetchFileTree('p1')
      await store.fetchFileTree('p1', true)
      await vi.advanceTimersByTimeAsync(10000)

      expect(getRequest).toHaveBeenCalledTimes(2)
      expect(visible).toEqual([fileNode])
      expect(store.isFileTreeDegraded('p1')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('isolates scheduled recovery by project key and applies authoritative deletion', async () => {
    vi.useFakeTimers()
    try {
      const deletedTree = { data: { data: { nodes: [], complete: true } } }
      getRequest.mockResolvedValueOnce(fileTreePartial)
        .mockResolvedValueOnce(fileTreeOk)
        .mockResolvedValueOnce(deletedTree)
      const store = useExplorerStore()
      const aVisible = await store.fetchFileTree('p1')
      await store.fetchFileTree('p2')
      await vi.advanceTimersByTimeAsync(1500)

      expect(aVisible).toEqual([])
      expect(store.fileTreeCache['p1:main']).toEqual([])
      expect(store.fileTreeCache['p2:main']).toEqual([fileNode])
      expect(store.isFileTreeDegraded('p1')).toBe(false)
      expect(store.isFileTreeDegraded('p2')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})
