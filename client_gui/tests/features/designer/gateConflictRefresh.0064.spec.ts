import { effectScope, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { useGateRecoveryRefresh } from '@/features/designer/resources/useGateRecoveryRefresh'
import { GATE_RUN_EVENTS, gateRunEventMatches } from '@/features/designer/resources/useGateContext'

afterEach(() => vi.useRealTimers())
describe('conflict completion refresh', () => {
  it('subscribes to real completion events scoped to the same flow', () => {
    expect(GATE_RUN_EVENTS).toContain('git_pending_changed')
    expect(GATE_RUN_EVENTS).toContain('git_finalize_done')
    expect(gateRunEventMatches({ groupId: 'other' }, { flowId: 'flow' })).toBe(false)
  })
  it('recovers a missed event while waiting, without overlapping reads, and stops on completion', async () => {
    vi.useFakeTimers()
    const waiting = ref(false)
    let finish!: () => void
    const reload = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
    const scope = effectScope()
    scope.run(() => useGateRecoveryRefresh(() => waiting.value, reload))
    await vi.advanceTimersByTimeAsync(10000)
    expect(reload).not.toHaveBeenCalled()
    waiting.value = true; await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(15000)
    expect(reload).toHaveBeenCalledTimes(1)
    waiting.value = false; finish(); await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)
    expect(reload).toHaveBeenCalledTimes(1)
    scope.stop()
  })
  it('cancels its pending read when the component leaves', async () => {
    vi.useFakeTimers()
    const reload = vi.fn(async () => true)
    const scope = effectScope()
    scope.run(() => useGateRecoveryRefresh(() => true, reload))
    scope.stop()
    await vi.advanceTimersByTimeAsync(10000)
    expect(reload).not.toHaveBeenCalled()
  })
})
