// Shared server envelopes exercise the real API adapter, parser, reader and row.
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import verificationRegistration from '@/features/designer/inspector/features/verification/register'
import { resetVerificationAnswers } from '@/features/designer/inspector/features/verification/useVerification'
import { createInspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import { prepareInspectorI18n } from '../support/inspectorSupport'
import contract from './stopFailureContract.v02test0084.json'

// Only the HTTP transport is mocked: API path/envelope/parser, reader and row stay real.
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async (original) => ({
  ...await original<typeof import('@shared/api')>(), getRequest: http.get, postRequest: http.post,
}))

const i18n = prepareInspectorI18n()
const FLOW = 'flowgate.v02test.0084'

function selection(): FlowGraphSelection {
  const node: FlowGraphNode = {
    id: 'gn_4', kind: 'task', title: '알림 큐 분리', state: 'in_progress', docType: 'T', x: 0, y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: 'doc_t', label: '알림 큐 분리', assignee: null, rootBound: false },
    document: null, slot: null,
    nodeRun: { status: 'running', openedAt: null, finishedAt: null, resultDocId: null, attemptId: 'att_1' },
  }
  const graph: FlowGraph = { flowId: FLOW, nodes: [node], edges: [], ownership: 'graph', draft: null, viewMode: 'revision' }
  return { projectId: 'flowgate', graph, node }
}
const SlotRows = defineComponent({ setup: () => () => h('div', [h(InspectorSlotRow, { name: '지시서', state: { label: '승인됨', tone: 'success' } })]) })
const fixture = createInspectorRegistry({
  './features/taskCard/register.ts': { default: { key: 'taskCard', order: 403, cardKinds: ['task'], requiresDocument: false, tab: { key: 'documents', component: SlotRows }, messages: { ko: {}, en: {}, ja: {} } } },
  './features/verification/register.ts': { default: verificationRegistration },
})

const wrappers: VueWrapper<any>[] = []
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
  resetVerificationAnswers()
  document.body.innerHTML = ''
})
beforeEach(() => {
  vi.resetAllMocks()
  i18n.global.locale.value = 'ko'
})

async function mountRow() {
  const w = mount(DesignerInspector, { props: { selection: selection(), registry: fixture }, attachTo: document.body, global: { plugins: [i18n] } })
  wrappers.push(w)
  await flushPromises()
  return w
}
const q = (w: VueWrapper<any>, test: string) => w.find(`[data-test="${test}"]`)
const badge = (w: VueWrapper<any>) => w.find('[data-test="verification-row"] [data-test="inspector-slot-state"]')
const buttons = (w: VueWrapper<any>) => w.findAll('[data-test="verification-row"] button:not([data-test="inspector-slot-name"])').map((b) => b.attributes('data-test'))
const dialog = (w: VueWrapper<any>) => w.findComponent(ConfirmDialog)

describe('server response -> HTTP adapter -> verification row', () => {
  const path = '/api/v1/flows/flowgate.v02test.0084/nodes/gn_4/self-check'
  it.each([
    ['host_changed', '서버가 바뀌어 확인할 수 없음'],
    ['processes_remain', '남은 프로세스가 있음'],
    ['owner_alive', '검증 작업이 아직 끝나지 않음'],
    ['ownership_unknown', '종료를 확인하지 못함'],
  ] as const)('renders the server reason %s and retries the same run', async (reason, message) => {
    http.get.mockResolvedValue({ data: contract[reason] })
    const w = await mountRow()
    expect(http.get).toHaveBeenCalledWith(path)
    expect(q(w, 'verification-reason').text()).toBe(message)
    expect(buttons(w)).toEqual(['verification-retry_stop', 'verification-mark_ended'])
    http.post.mockResolvedValue({ data: contract.host_changed })
    await q(w, 'verification-retry_stop').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith(path + '/runs/scr_1/force-stop', {})
    expect(q(w, 'verification-reason').text()).toBe('서버가 바뀌어 확인할 수 없음')
  })

  it.each(['retry_stop', 'mark_ended'] as const)('%s renders stopped then hides on the next read', async (button) => {
    vi.useFakeTimers()
    try {
      http.get.mockResolvedValueOnce({ data: contract.processes_remain }).mockResolvedValue({ data: contract.hidden })
      http.post.mockResolvedValue({ data: contract.stopped })
      const w = await mountRow()
      await q(w, 'verification-' + button).trigger('click')
      if (button === 'mark_ended') {
        expect(http.post).not.toHaveBeenCalled()
        dialog(w).vm.$emit('confirm')
      }
      await flushPromises()
      expect(http.post).toHaveBeenCalledWith(path + '/runs/scr_1/' + (button === 'mark_ended' ? 'mark-ended' : 'force-stop'), {})
      expect(badge(w).text()).toBe('종료됨')
      expect(q(w, 'verification-reason').exists()).toBe(false)
      expect(buttons(w)).toEqual([])
      await vi.advanceTimersByTimeAsync(3000)
      await flushPromises()
      expect(http.get).toHaveBeenCalledTimes(2)
      expect(q(w, 'verification-row').exists()).toBe(false)
    } finally {
      wrappers.splice(0).forEach((w) => w.unmount())
      vi.useRealTimers()
    }
  })

  it.each(['selfcheck_mark_ended_not_applicable', 'selfcheck_state_conflict'])('refreshes on the server rejection %s', async (code) => {
    http.get.mockResolvedValueOnce({ data: contract.processes_remain }).mockResolvedValue({ data: contract.host_changed })
    http.post.mockRejectedValue({ response: { status: 409, data: { ok: false, error: { code, message: code, params: {} } } } })
    const w = await mountRow()
    await q(w, 'verification-mark_ended').trigger('click')
    dialog(w).vm.$emit('confirm')
    await flushPromises()
    expect(http.get).toHaveBeenCalledTimes(2)
    expect(q(w, 'verification-reason').text()).toBe('서버가 바뀌어 확인할 수 없음')
    expect(dialog(w).props('open')).toBe(false)
  })
})
