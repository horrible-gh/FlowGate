import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
const fetchGitExecution = vi.hoisted(() => vi.fn())
vi.mock('@/features/git', () => ({ fetchGitExecution }))
import { gitTabActivity, observeGitTabActivity, pruneGitTabActivity } from '@/features/designer/center/gitTabActivity'

afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); pruneGitTabActivity(new Set()) })

describe('server Git activity in inactive Flow tabs', () => {
  it('updates all open tabs and stops its timer when the Center unmounts', async () => {
    vi.useFakeTimers()
    let complete = false
    fetchGitExecution.mockImplementation(async () => ({ status: complete ? 'complete' : 'running',
      steps: ['commit', 'update', 'merge', 'push'], cursor: complete ? 4 : 2, error: null }))
    const stop = observeGitTabActivity(() => ['A', 'B'])
    await flushPromises()
    expect(gitTabActivity.A).toEqual({ state: 'running', step: 'merge', code: null })
    expect(gitTabActivity.B.state).toBe('running')
    complete = true
    await vi.advanceTimersByTimeAsync(3100)
    expect(gitTabActivity.A.state).toBe('done')
    expect(gitTabActivity.B.state).toBe('done')
    stop()
    const calls = fetchGitExecution.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetchGitExecution).toHaveBeenCalledTimes(calls)
  })

  it('ignores a late answer for a closed tab', async () => {
    let resolve!: (value: unknown) => void
    fetchGitExecution.mockImplementation(() => new Promise(r => { resolve = r }))
    let tabs = ['A']
    const stop = observeGitTabActivity(() => tabs)
    tabs = []
    resolve({ status: 'complete', steps: [], cursor: 0 })
    await flushPromises()
    expect(gitTabActivity.A).toBeUndefined()
    stop()
  })
})
