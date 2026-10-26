/** D0007 §8-10: use the existing Git detail window for retry/cancel/resolve. */
import { computed, defineComponent, h, provide } from 'vue'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ read: vi.fn(), recover: vi.fn() }))
vi.mock('@/features/document', async original => ({ ...await original<typeof import('@/features/document')>(), runCurrentRead: (...a: unknown[]) => mocks.read(...a), runGateRecover: (...a: unknown[]) => mocks.recover(...a) }))
import GitPushErrorWindow from '@/features/git/GitPushErrorWindow.vue'
import { useGateOperations, GATE_OPERATIONS_CONTEXT, type GateOperations } from '@/features/git/gateOperations'
import { GIT_PANEL_CONTEXT } from '@/features/git/gitSession'
import { prepareInspectorI18n } from '../designer/inspector/support/inspectorSupport'
let current: any
let jobs: GateOperations
let wrapper: VueWrapper | null = null
let readOnly = false
const close = vi.fn()
const i18n = prepareInspectorI18n()
const node = () => ({ nodeKey: 'g1', status: 'open', attemptId: 'a1', gate: {
  decision_in_progress: true, can_decide: false, settings_locked: true, archived: false, recovery_reset_required: false,
  decision: { decision_id: 'd1', status: 'recovery_required', effects: [{ phase: 'push', status: 'unknown', sha_after: 'abc' }] },
  job: { job_id: 'j1', status: 'recovery_required', phase: 'push' },
  recovery: { decision_id: 'd1', decision_version: 7, status: 'recovery_required', retryable: true, cancelable: false, resolvable: true,
    evidence_version: 4, evidence_refs: ['d1/push@2'], phase: 'push', error_code: 'push_timeout' },
} })
async function show() {
  wrapper = mount(defineComponent({ setup() {
    jobs = useGateOperations(() => 'flowgate', () => 'flowgate.v02.0085', () => readOnly)
    provide(GATE_OPERATIONS_CONTEXT, jobs)
    provide(GIT_PANEL_CONTEXT, { session: { state: { pushFailure: null }, closeWindow: close, setWindowMinimized: vi.fn() } } as any)
    return () => h(GitPushErrorWindow, { win: { id: 'gate-job:flowgate.v02.0085', kind: 'push-error', title: 'Git job', minimized: false, groupId: 'flowgate.v02.0085', mergeId: null }, operation: jobs.selected.value })
  } }), { global: { plugins: [i18n], stubs: { ModelessShell: { template: '<div><slot /></div>' } } } })
  await flushPromises()
  jobs.select(jobs.operations.value[0]); await flushPromises()
  return wrapper
}
beforeEach(() => {
  readOnly = false; close.mockReset(); mocks.read.mockReset(); mocks.recover.mockReset()
  current = { runId: 'r1', status: 'idle', nodes: [node()] }
  mocks.read.mockImplementation(async () => current)
  mocks.recover.mockImplementation(async () => current = { ...current, nodes: [] })
})
afterEach(() => { wrapper?.unmount(); wrapper = null })
describe('existing Git operation details — D §8-10', () => {
  it.each(['retry', 'cancel'])('routes the common %s action using the server flags and version', async action => {
    current.nodes[0].gate.recovery.cancelable = action === 'cancel'
    const w = await show()
    expect(w.get('[data-test="git-job-evidence"]').text()).toContain('j1')
    await w.get(`[data-test="git-job-${action}"]`).trigger('click'); await flushPromises()
    expect(mocks.recover).toHaveBeenCalledWith('r1', 'g1', 'd1', action, { request_id: expect.any(String), expected_decision_version: 7 })
    expect(close).toHaveBeenCalled()
  })
  it.each([['effects_confirmed', 'resolve'], ['abandon', 'abandon']])('requires review/reason before %s and sends the reviewed evidence snapshot', async (resolution, test) => {
    const w = await show()
    expect(w.get(`[data-test="git-job-${test}"]`).attributes('disabled')).toBeDefined()
    // A later read cannot silently replace the evidence the person opened.
    current = { ...current, nodes: [{ ...node(), gate: { ...node().gate, recovery: { ...node().gate.recovery, decision_version: 8, evidence_version: 5, evidence_refs: ['d1/push@3'] } } }] }
    await jobs.reload()
    await w.get('[data-test="git-job-reviewed"]').setValue(true)
    await w.get('[data-test="git-job-reason"]').setValue('remote result reviewed')
    await w.get(`[data-test="git-job-${test}"]`).trigger('click'); await flushPromises()
    expect(mocks.recover).toHaveBeenCalledWith('r1', 'g1', 'd1', 'resolve', {
      request_id: expect.any(String), expected_decision_version: 7, resolution, reason: 'remote result reviewed', evidence_version: 4, evidence_refs: ['d1/push@2'],
    })
  })
  it('uses recorded target restrictions and local effects in recovery without disabling the existing retry', async () => {
    current.nodes[0].gate.decision.settings = { shown: { merge_target_branch: 'release/local' }, effective: { merge_mode: 'merge', push: true }, target_basis: { target_branch: 'release/local', unmerge_supported: false, warnings: ['unmerge_unsupported_for_non_base_target'] } }
    current.nodes[0].gate.decision.effects.unshift({ phase: 'merge', status: 'done', sha_after: 'local-merge' })
    current.nodes[0].gate.recovery.error_code = 'push_rejected'
    const w = await show()
    expect(w.get('[data-test="gate-summary-target"]').text()).toBe('release/local')
    expect(w.get('[data-test="gate-summary-unmerge"]').text()).toBe('이 대상은 병합 취소 미지원')
    expect(w.get('[data-test="gate-summary-error"]').text()).toBe('원격 갱신 거절 · 로컬 반영 보존')
    expect(w.get('[data-test="gate-unmerge"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="git-job-retry"]').attributes('disabled')).toBeUndefined()
    expect(mocks.recover).not.toHaveBeenCalled()
  })
  it('does not offer no-effect cancellation for partial/unknown effects', async () => {
    const w = await show()
    expect(w.get('[data-test="git-job-cancel"]').attributes('disabled')).toBeDefined()
    expect(await jobs.act('cancel')).toBe(false)
    expect(mocks.recover).not.toHaveBeenCalled()
  })
  it('GETs before resending the identical recovery request after network loss', async () => {
    const order: string[] = []
    mocks.read.mockImplementation(async () => { order.push('get'); return current })
    mocks.recover.mockImplementationOnce(async () => { order.push('post'); throw { request: {} } }).mockImplementationOnce(async () => { order.push('replay'); return { ...current, nodes: [] } })
    const w = await show(); order.length = 0
    await w.get('[data-test="git-job-retry"]').trigger('click'); await flushPromises()
    expect(order).toEqual(['post', 'get', 'replay'])
    expect(mocks.recover.mock.calls[1]).toEqual(mocks.recover.mock.calls[0])
  })
  it('keeps common controls read only and rejects a version refusal without synthesizing completion', async () => {
    const w = await show()
    readOnly = true
    expect(await jobs.act('retry')).toBe(false)
    expect(mocks.recover).not.toHaveBeenCalled()
    readOnly = false
    mocks.recover.mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { error: { code: 'gate_context_changed' } } } })
    await w.get('[data-test="git-job-retry"]').trigger('click'); await flushPromises()
    expect(jobs.selected.value?.gate.decision_in_progress).toBe(true)
    expect(jobs.notice.value).toBe('gate_context_changed')
    expect(close).not.toHaveBeenCalled()
  })
})
