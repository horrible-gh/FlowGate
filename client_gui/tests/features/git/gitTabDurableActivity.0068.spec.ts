import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import type { FlowGitExecution } from '@/features/project'
import {
  clearGitTabResult, gitTabActivity, observeGitTabActivity, pruneGitTabActivity, setActiveGitTab, setGitTabActivity,
} from '@/features/designer/center/gitTabActivity'

const STEPS: FlowGitExecution['steps'] = ['commit', 'update', 'merge', 'push']
const run = (over: Partial<FlowGitExecution> = {}): FlowGitExecution =>
  ({ jobId: 'job-1', status: 'running', steps: STEPS, cursor: 2, error: null, skipped: {}, ...over })
const sourceOf = (answer: (flowId: string) => FlowGitExecution | null | Promise<FlowGitExecution | null>) => {
  const loadRun = vi.fn(async (flowId: string) => answer(flowId))
  return { loadRun }
}

afterEach(() => { vi.useRealTimers(); pruneGitTabActivity(new Set()) })

describe('server Git runs in the Flow tabs (flowgate.v02test.0068)', () => {
  it('reads every open tab once, polls only running runs, and stops with the Center', async () => {
    vi.useFakeTimers()
    let done = false
    const source = sourceOf((id) => id === 'A'
      ? run(done ? { status: 'complete', cursor: 4 } : {})
      : run({ jobId: 'job-b', status: 'complete', cursor: 4 }))
    const stop = observeGitTabActivity(() => ['A', 'B'], source, 3000)
    await flushPromises()
    expect(gitTabActivity.A).toEqual({ state: 'running', step: 'merge', code: null })
    // B had ended before the screen first read it: an old result, no mark.
    expect(gitTabActivity.B).toBeUndefined()
    done = true
    await vi.advanceTimersByTimeAsync(3100)
    expect(gitTabActivity.A.state).toBe('done')
    // B ended before it was first read: it is never read again.
    expect(source.loadRun.mock.calls.filter(([id]) => id === 'B')).toHaveLength(1)
    stop()
    const calls = source.loadRun.mock.calls.length
    await vi.advanceTimersByTimeAsync(9000)
    expect(source.loadRun).toHaveBeenCalledTimes(calls)
  })

  it.each([
    ['complete', run({ status: 'complete', cursor: 4 })],
    ['stopped', run({ status: 'stopped', cursor: 3, error: 'push_rejected' })],
    ['remaining', run({ status: 'remaining', cursor: 4 })],
    ['skipped update', run({ status: 'complete', cursor: 4, skipped: { update: 'git_busy' } })],
  ] as const)('does not light a tab for an old %s run when it opens', async (_name, old) => {
    const source = sourceOf(() => old)
    const stop = observeGitTabActivity(() => ['A', 'B'], source, 3000)
    await flushPromises()
    expect(gitTabActivity).toEqual({})
    stop()
  })

  it('does not bring back a result the tab cleared', async () => {
    vi.useFakeTimers()
    let stopped = false
    const source = sourceOf(() => stopped ? run({ status: 'stopped', cursor: 3, error: 'push_rejected' }) : run())
    const stop = observeGitTabActivity(() => ['A'], source, 3000)
    await flushPromises()
    stopped = true
    await vi.advanceTimersByTimeAsync(3100)
    expect(gitTabActivity.A).toEqual({ state: 'failed', step: 'push', code: 'push_rejected' })
    clearGitTabResult('A')
    await vi.advanceTimersByTimeAsync(9000)
    expect(gitTabActivity.A).toBeUndefined()
    stop()
  })

  it('shows work left after a run as remaining, the way the run box does', async () => {
    vi.useFakeTimers()
    let ended = false
    const source = sourceOf(() => run(ended ? { status: 'remaining', cursor: 4 } : {}))
    const stop = observeGitTabActivity(() => ['A'], source, 3000)
    await flushPromises()
    ended = true
    await vi.advanceTimersByTimeAsync(3100)
    expect(gitTabActivity.A).toEqual({ state: 'remaining', step: null, code: null })
    stop()
  })

  it('drops the shown tab\'s result the moment its run ends, and keeps it on another tab', async () => {
    vi.useFakeTimers()
    let ended = false
    const source = sourceOf(() => run(ended ? { status: 'complete', cursor: 4 } : {}))
    setActiveGitTab('A')
    const stop = observeGitTabActivity(() => ['A', 'B'], source, 3000)
    await flushPromises()
    expect(gitTabActivity.A?.state).toBe('running')
    expect(gitTabActivity.B?.state).toBe('running')
    ended = true
    await vi.advanceTimersByTimeAsync(3100)
    expect(gitTabActivity.A).toBeUndefined()
    expect(gitTabActivity.B).toEqual({ state: 'done', step: null, code: null })
    setActiveGitTab('B')
    expect(gitTabActivity.B).toBeUndefined()
    stop()
  })

  it('leaves a run the panel reports to the panel, and reads it again once the panel lets go', async () => {
    vi.useFakeTimers()
    const source = sourceOf(() => run())
    setGitTabActivity('A', { state: 'running', step: 'commit', code: null })
    const stop = observeGitTabActivity(() => ['A'], source, 3000)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(9000)
    // The first read only; the panel's own poll follows the run.
    expect(source.loadRun).toHaveBeenCalledTimes(1)
    expect(gitTabActivity.A).toEqual({ state: 'running', step: 'commit', code: null })
    // Another Flow shown: the panel drops its mark, the run goes on in the server.
    setGitTabActivity('A', null)
    await vi.advanceTimersByTimeAsync(3100)
    expect(gitTabActivity.A).toEqual({ state: 'running', step: 'merge', code: null })
    stop()
  })

  it('shows the result of a run the panel let go of even if it ended meanwhile', async () => {
    vi.useFakeTimers()
    const source = sourceOf(() => run({ status: 'complete', cursor: 4 }))
    setGitTabActivity('A', { state: 'running', step: 'push', code: null })
    const stop = observeGitTabActivity(() => ['A'], source, 3000)
    await flushPromises()
    setGitTabActivity('A', null)
    await vi.advanceTimersByTimeAsync(3100)
    expect(gitTabActivity.A).toEqual({ state: 'done', step: null, code: null })
    stop()
  })

  it('ignores a late answer for a closed tab', async () => {
    let resolve!: (value: FlowGitExecution | null) => void
    const source = sourceOf(() => new Promise((r) => { resolve = r }))
    let tabs = ['A']
    const stop = observeGitTabActivity(() => tabs, source, 3000)
    tabs = []
    resolve(run())
    await flushPromises()
    expect(gitTabActivity.A).toBeUndefined()
    stop()
  })
})
