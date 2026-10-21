import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async (original) => ({
  ...await original<typeof import('@shared/api')>(), getRequest: http.get, postRequest: http.post,
}))
const api = vi.hoisted(() => ({ read: vi.fn(), recover: vi.fn() }))
vi.mock('@/features/document', async (original) => ({
  ...await original<typeof import('@/features/document')>(),
  runCurrentRead: (...args: unknown[]) => api.read(...args),
  runCardRecover: (...args: unknown[]) => api.recover(...args),
}))
import { runCardRecover as recoverEndpoint, toRunCurrent } from '@/features/document/api/run'
import CardRecovery from '@/features/designer/inspector/features/cardRecovery/CardRecovery.vue'
import messages from '@/features/designer/inspector/features/cardRecovery/messages'
import registration from '@/features/designer/inspector/features/cardRecovery/register'
import { INSPECTOR_HOST } from '@/features/designer/inspector/inspectorHost'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'

const target = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({
  projectId: 'flowgate', flowId: 'flow', nodeId: 'card', nodeKind: 'plan', docId: null, docType: 'WP',
  cardKind: 'plan', title: 'WP', canvasState: null, docReviewStatus: null, docStatus: null, supersedes: [],
  slot: null, nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null },
  cardDocuments: [], nodeType: 'document', cardDocType: 'WP', rootBound: false, ownership: 'graph',
  viewMode: 'revision', readOnly: 'revision', editLock: null, ...fields,
})
const idle = { runId: 'run', status: 'idle', idleReason: 'waiting_approval', nodes: [{ nodeKey: 'card', status: 'open', resultDocId: null }] }
const request = vi.fn()
let wrappers: ReturnType<typeof mount>[] = []
function render(value = target()) {
  const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko: { gui: { inspector: { cardRecovery: messages.ko } } } } })
  const wrapper = mount(CardRecovery, { props: { target: value }, global: { plugins: [i18n], provide: { [INSPECTOR_HOST as symbol]: { request } } } })
  wrappers.push(wrapper)
  return wrapper
}
beforeEach(() => {
  vi.clearAllMocks()
  api.read.mockResolvedValue(idle)
  api.recover.mockResolvedValue({ ...idle, nodes: [{ nodeKey: 'card', status: 'pending' }] })
})
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers = [] })

describe('0083 WP-2 Inspector card recovery', () => {
  it('recovers one card, refreshes its flow, and waits for an explicit Resume', async () => {
    const w = render()
    await flushPromises()
    await w.get('[data-test=card-recover]').trigger('click')
    await flushPromises()
    expect(api.recover).toHaveBeenCalledTimes(1)
    expect(api.recover).toHaveBeenCalledWith('run', 'card')
    expect(request).toHaveBeenCalledWith({ kind: 'refresh-flow', flowId: 'flow' })
    expect(w.text()).toContain(messages.ko.done)
    expect(w.find('[data-test=card-recover]').exists()).toBe(false)
    expect(registration.tab?.key).toBe('documents')
  })
  it.each(['running', 'paused', 'stopping', 'stopped'])('disables recovery for %s runs', async status => {
    api.read.mockResolvedValue({ ...idle, status: status === 'stopped' ? 'idle' : status, idleReason: status })
    const w = render()
    await flushPromises()
    expect(w.get('[data-test=card-recover]').attributes('disabled')).toBeDefined()
    expect(api.recover).not.toHaveBeenCalled()
  })
  it.each(['review', 'done', 'pending'])('hides recovery for %s cards', async status => {
    const w = render(target({ nodeRun: { status, openedAt: null, finishedAt: null, resultDocId: null } }))
    await flushPromises()
    expect(w.find('[data-test=card-recover]').exists()).toBe(false)
  })
  it('does not allow root, conflicted, locked or result-bearing cards', async () => {
    for (const fields of [{ rootBound: true }, { ownership: 'conflict', readOnly: 'conflict' as const },
      { editLock: 'run' as const }, { editLock: 'unknown' as const },
      { nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: 'result' } }]) {
      const w = render(target(fields))
      await flushPromises()
      expect(w.find('[data-test=card-recover]').exists()).toBe(false)
    }
  })
  it('keeps recovery disabled when reading fails', async () => {
    api.read.mockRejectedValue(new Error('offline'))
    const w = render()
    await flushPromises()
    expect(w.get('[data-test=card-recover]').attributes('disabled')).toBeDefined()
  })
  it.each([403, 409])('shows server refusal %s and refreshes', async status => {
    api.recover.mockRejectedValue({ isAxiosError: true, response: { status, data: { error: { code: 'card_recovery_refused', message: 'Recovery refused' } } } })
    const w = render()
    await flushPromises()
    await w.get('[data-test=card-recover]').trigger('click')
    await flushPromises()
    expect(w.get('[role=alert]').text()).toBe('Recovery refused')
    expect(request).toHaveBeenCalledWith({ kind: 'refresh-flow', flowId: 'flow' })
  })
  it('does not send a second request or show a late result on another card', async () => {
    let resolve!: (value: unknown) => void
    api.recover.mockReturnValue(new Promise(r => { resolve = r }))
    const w = render()
    await flushPromises()
    await w.get('[data-test=card-recover]').trigger('click')
    await w.get('[data-test=card-recover]').trigger('click')
    expect(api.recover).toHaveBeenCalledTimes(1)
    await w.setProps({ target: target({ nodeId: 'other', flowId: 'other-flow' }) })
    resolve(idle)
    await flushPromises()
    expect(w.find('[role=status]').exists()).toBe(false)
    expect(request).toHaveBeenCalledWith({ kind: 'refresh-flow', flowId: 'flow' })
  })
})


it('uses the single encoded recovery endpoint and retains the existing idle reason', async () => {
  http.post.mockResolvedValue({ data: { run: { run_id: 'run', status: 'idle', idle_reason: 'no_targets' } } })
  const result = await recoverEndpoint('run/a', 'card/b')
  expect(http.post).toHaveBeenCalledWith('/api/v1/runs/run%2Fa/nodes/card%2Fb/recover', {})
  expect(result?.idleReason).toBe('no_targets')
  expect(toRunCurrent({ run_id: 'human', status: 'idle', idle_reason: 'waiting_approval' })?.idleReason).toBe('waiting_approval')
})
