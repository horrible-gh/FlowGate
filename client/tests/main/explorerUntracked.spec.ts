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
