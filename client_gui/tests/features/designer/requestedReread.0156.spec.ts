import { afterEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import { fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'
import type { FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'

// 0156 T3: no wall-clock wait, requests, or mounted application.
afterEach(() => vi.useRealTimers())
describe('requested reread timer (0156)', () => {
  it.each(['empty', 'paused'] as const)('rearms while requested and stops when %s', async (ending) => {
    vi.useFakeTimers()
    let run: FlowRunInfo = { runId: 'run', status: 'running', openNodeKeys: [], requestedExecutionCount: 1 }
    const loadRun = vi.fn(async () => run)
    const source = { ...fixtureFlowGraphSource, loadRun, startRun: vi.fn(async () => run),
      resumeRun: vi.fn(async () => run), stopRun: vi.fn(async () => run) }
    const control = createFlowRunControl({ source, projectId: () => 'p', isOpen: () => true,
      prepare: () => true, refresh: vi.fn() }, reactive<Record<string, FlowRunState>>({}))
    await control.read('p.ui.0001')
    expect(loadRun).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(loadRun).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2000)
    expect(loadRun).toHaveBeenCalledTimes(3)
    run = { ...run, ...(ending === 'empty' ? { requestedExecutionCount: 0 } : { status: 'paused' as const }) }
    await control.read('p.ui.0001')
    const count = loadRun.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(loadRun).toHaveBeenCalledTimes(count)
  })
})
