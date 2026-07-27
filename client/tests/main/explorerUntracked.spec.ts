/**
 * 0308 TR (NR0003 권고 1·6) — the file tree surfaces new (untracked) base-checkout files
 * on a channel SEPARATE from base_dirty.
 *
 * Bug 0308.0001-B: new files in the Git working tree were invisible in the file explorer.
 * The tree's only marker reflected tracked-file changes (base_dirty), and base_dirty
 * deliberately excludes untracked files so it never widens the E3 merge-finalize guard
 * (git_service.py). The server already emits them as base_untracked; the store now keeps
 * them on their own channel and exposes isBaseUntracked{Path,Dir} for the new-file badge.
 * These regression tests pin that the two channels stay disjoint and that the new marker
 * propagates to ancestor folders (so a new file inside a collapsed folder is still visible).
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useExplorerStore } from '@main/stores/explorer'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest }))

const statusOk = (status: any) => ({ data: { ok: true, status } })

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  localStorage.clear()
})

describe('explorer store — new (untracked) file markers', () => {
  it('populates baseUntrackedFiles from base_untracked, separate from base_dirty', async () => {
    getRequest.mockResolvedValueOnce(
      statusOk({
        enabled: true,
        base_dirty: { dirty: true, files: ['src/tracked.ts'] },
        base_untracked: { count: 1, files: ['src/brand-new.ts'], truncated: false },
      }),
    )
    const store = useExplorerStore()

    await store.fetchGitStatus('p1')

    // The new file is reported ONLY on the untracked channel, never as dirty (E3 guard).
    expect(store.isBaseUntrackedPath('p1', 'src/brand-new.ts')).toBe(true)
    expect(store.isBaseDirtyPath('p1', 'src/brand-new.ts')).toBe(false)
    // The tracked change stays on the dirty channel and is not mistaken for new.
    expect(store.isBaseDirtyPath('p1', 'src/tracked.ts')).toBe(true)
    expect(store.isBaseUntrackedPath('p1', 'src/tracked.ts')).toBe(false)
  })

  it('propagates the new marker to ancestor folders (collapsed-folder reveal)', async () => {
    getRequest.mockResolvedValueOnce(
      statusOk({ enabled: true, base_untracked: { count: 1, files: ['a/b/c/new.ts'] } }),
    )
    const store = useExplorerStore()

    await store.fetchGitStatus('p1')

    expect(store.isBaseUntrackedDir('p1', 'a')).toBe(true)
    expect(store.isBaseUntrackedDir('p1', 'a/b')).toBe(true)
    expect(store.isBaseUntrackedDir('p1', 'a/b/c')).toBe(true)
    expect(store.isBaseUntrackedDir('p1', 'other')).toBe(false)
  })

  it('normalizes backslash paths and clears the markers when a later status reports none', async () => {
    getRequest
      .mockResolvedValueOnce(
        statusOk({ enabled: true, base_untracked: { count: 1, files: ['dir\\win.ts'] } }),
      )
      .mockResolvedValueOnce(statusOk({ enabled: true, base_dirty: { dirty: false, files: [] } }))
    const store = useExplorerStore()

    await store.fetchGitStatus('p1')
    expect(store.isBaseUntrackedPath('p1', 'dir/win.ts')).toBe(true)

    // A later status without base_untracked clears them (the files were committed/removed).
    await store.fetchGitStatus('p1')
    expect(store.isBaseUntrackedPath('p1', 'dir/win.ts')).toBe(false)
  })
})

/**
 * 0315 TR (NR0003 권고 1·2·3·4) — the READ-ONLY group-branch explorer now surfaces new
 * (untracked) files too. B0001: a worker's just-created file was invisible in the group
 * branch view until finalize, because the checkout-free tree/changes/blob reads see
 * committed git objects only. The tree read now returns a worktree_untracked channel;
 * the changes read lists new files with a '?' status; untracked blobs are read off disk
 * (commit=null) and never cached by commit. These pin that store behaviour.
 */
describe('explorer store — group-branch new (untracked) file markers', () => {
  const treeResp = (worktreeUntracked: string[], nodes: any[] = []) => ({
    data: { data: { branch: 'g_default_0315', commit: 'abc123', nodes, worktree_untracked: worktreeUntracked } },
  })

  it('populates groupUntrackedFiles from the tree worktree_untracked channel, apart from changed', async () => {
    getRequest.mockResolvedValueOnce(treeResp(['pkg\\new.py']))
    const store = useExplorerStore()

    await store.fetchGroupBranchTree('p1', 'g1')

    // Backslashes normalized; the new file is on the untracked channel, not the changed one.
    expect(store.isGroupUntrackedPath('p1', 'g1', 'pkg/new.py')).toBe(true)
    expect(store.isGroupChangedPath('p1', 'g1', 'pkg/new.py')).toBe(false)
  })

  it('propagates the group new marker to ancestor folders (collapsed-folder reveal)', async () => {
    getRequest.mockResolvedValueOnce(treeResp(['a/b/c/new.py']))
    const store = useExplorerStore()

    await store.fetchGroupBranchTree('p1', 'g1')

    expect(store.isGroupUntrackedDir('p1', 'g1', 'a')).toBe(true)
    expect(store.isGroupUntrackedDir('p1', 'g1', 'a/b')).toBe(true)
    expect(store.isGroupUntrackedDir('p1', 'g1', 'a/b/c')).toBe(true)
    expect(store.isGroupUntrackedDir('p1', 'g1', 'other')).toBe(false)
  })

  it("keeps the changes list full but routes '?' untracked out of the MODIFIED badge", async () => {
    getRequest.mockResolvedValueOnce({
      data: { data: { changes: [
        { path: 'edited.py', status: 'M' },
        { path: 'brand-new.py', status: '?' },
      ] } },
    })
    const store = useExplorerStore()

    const changes = await store.fetchGroupBranchChanges('p1', 'g1')

    expect(changes.length).toBe(2) // the endpoint's full list is still returned to callers
    expect(store.isGroupChangedPath('p1', 'g1', 'edited.py')).toBe(true)
    // The untracked file must NOT read as modified — it belongs to the NEW badge instead.
    expect(store.isGroupChangedPath('p1', 'g1', 'brand-new.py')).toBe(false)
  })

  it('preserves tracked statuses and clears stale deletion state on refresh', async () => {
    getRequest
      .mockResolvedValueOnce({
        data: { data: { changes: [
          { path: 'docs\\gone.md', status: 'D' },
          { path: 'docs/edited.md', status: 'M' },
        ] } },
      })
      .mockResolvedValueOnce({
        data: { data: { changes: [{ path: 'docs/edited.md', status: 'M' }] } },
      })
    const store = useExplorerStore()

    await store.fetchGroupBranchChanges('p1', 'g1')

    expect(store.groupChangeStatus('p1', 'g1', 'docs/gone.md')).toBe('D')
    expect(store.isGroupDeletedPath('p1', 'g1', 'docs/gone.md')).toBe(true)
    expect(store.isGroupDeletedPath('p1', 'g1', 'docs/edited.md')).toBe(false)

    await store.fetchGroupBranchChanges('p1', 'g1')

    expect(store.groupChangeStatus('p1', 'g1', 'docs/gone.md')).toBeUndefined()
    expect(store.isGroupDeletedPath('p1', 'g1', 'docs/gone.md')).toBe(false)
  })

  it('invalidates changed paths and their status map together', async () => {
    getRequest.mockResolvedValueOnce({
      data: { data: { changes: [{ path: 'gone.md', status: 'D' }] } },
    })
    const store = useExplorerStore()

    await store.fetchGroupBranchChanges('p1', 'g1')
    store.invalidateProject('p1')

    expect(store.isGroupChangedPath('p1', 'g1', 'gone.md')).toBe(false)
    expect(store.isGroupDeletedPath('p1', 'g1', 'gone.md')).toBe(false)
  })

  it('serves an untracked blob fresh every time (commit=null → never cached)', async () => {
    const blob = (content: string) => ({
      data: { data: {
        group_id: 'g1', branch: 'b', commit: null, path: 'new.py',
        size: content.length, binary: false, truncated: false,
        encoding: 'utf-8', content, untracked: true,
      } },
    })
    getRequest.mockResolvedValueOnce(blob('v1')).mockResolvedValueOnce(blob('v2'))
    const store = useExplorerStore()

    const first = await store.fetchGroupBranchBlob('p1', 'g1', 'new.py')
    const second = await store.fetchGroupBranchBlob('p1', 'g1', 'new.py')

    expect(first.content).toBe('v1')
    // A committed blob would have been cache-served; the untracked one is re-fetched so a
    // later edit (same commit-less state) is never masked by a stale cache entry.
    expect(second.content).toBe('v2')
    expect(getRequest).toHaveBeenCalledTimes(2)
  })
})
