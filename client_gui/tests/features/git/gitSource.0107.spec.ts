import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useProjectStore } from '@/features/project'
import type { GitWorktreeSource } from '@/features/git'
import { createHttpGitSource } from '@/features/git/gitSource'
import { G } from './fakeGitSource'
import { flowWorkBase } from './fakeWorktreeSource'

// flowgate.v02.0107 T#1 — the HTTP GitSource reads the Flow work base for display through the
// project store's side-effect-free boundary (D §3.1), never through the 0104 settings loader.

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('createHttpGitSource().worktree.loadWorkBase', () => {
  it('goes through readFlowWorkBaseForDisplay and hands its answer back as it is', async () => {
    const store = useProjectStore()
    const answer = flowWorkBase(G, { effectiveWorkBaseRef: 'feature/v0.2.0' })
    const display = vi.spyOn(store, 'readFlowWorkBaseForDisplay').mockResolvedValue(answer)
    const settings = vi.spyOn(store, 'loadFlowWorkBase')
    const worktree = createHttpGitSource().worktree as GitWorktreeSource
    await expect(worktree.loadWorkBase(G)).resolves.toBe(answer)
    expect(display).toHaveBeenCalledWith(G)
    expect(display).toHaveBeenCalledTimes(1)
    expect(settings).not.toHaveBeenCalled()
  })

  it('passes a refusal on untouched (the session decides what it shows)', async () => {
    const store = useProjectStore()
    const refusal = Object.assign(new Error(''), { status: 404, code: null })
    vi.spyOn(store, 'readFlowWorkBaseForDisplay').mockRejectedValue(refusal)
    const worktree = createHttpGitSource().worktree as GitWorktreeSource
    await expect(worktree.loadWorkBase(G)).rejects.toBe(refusal)
  })
})
