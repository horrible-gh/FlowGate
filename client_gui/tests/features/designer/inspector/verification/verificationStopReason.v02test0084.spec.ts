/**
 * flowgate.v02test.0084 #13 — the `종료 실패` line says why the stop could not be confirmed (`stop_reason`: host_changed /
 * processes_remain / owner_alive, any other code in general words) and may offer [끝난 것으로 처리] (`mark_ended`) next to
 * [다시 종료], asked in a dialog first. A new [다시 종료] answer brings its own reason. An older server (no `stop_reason`,
 * no `mark_ended`) draws the line as before; other states are untouched.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import verificationRegistration from '@/features/designer/inspector/features/verification/register'
import { resetVerificationAnswers } from '@/features/designer/inspector/features/verification/useVerification'
import {
  BUTTON_ACTION,
  verificationRowView,
  verificationStopReason,
} from '@/features/designer/inspector/features/verification/verificationModel'
import { createInspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import { toCardVerification } from '@/features/document/api/cardVerification'
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import { prepareInspectorI18n } from '../support/inspectorSupport'

const api = vi.hoisted(() => ({ read: vi.fn(), act: vi.fn(), post: vi.fn() }))
vi.mock('@/features/document/api/cardVerification', async (original) => ({
  ...await original<typeof import('@/features/document/api/cardVerification')>(),
  cardVerificationRead: api.read,
  cardVerificationAct: api.act,
}))

const i18n = prepareInspectorI18n()
const t = (key: string, params: Record<string, unknown> = {}) => i18n.global.t(key, params)
const k = (key: string) => `gui.inspector.verification.${key}`
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
const runRaw = (over: Record<string, unknown> = {}) => ({
  self_check_run_id: 'scr_1', status: 'running', started_at: '2026-11-19T08:42:00Z', enqueued_at: '2026-11-19T08:41:00Z',
  finished_at: null, show_at: '2026-11-19T08:46:00Z', elapsed_seconds: 300, ...over,
})
const raw = (over: Record<string, unknown> = {}) => ({
  flow_id: FLOW, node_key: 'gn_4', visible: true, state: 'overdue', run: runRaw(), blocker: null, actions: ['force_stop'], ...over,
})
const failed = (over: Record<string, unknown> = {}) =>
  raw({ state: 'stop_failed', actions: ['force_stop', 'mark_ended'], stop_reason: 'processes_remain', ...over })
const v = (body: Record<string, unknown>) => toCardVerification(body)

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
  vi.clearAllMocks()
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

describe('answer and model', () => {
  it('reads stop_reason and the mark_ended action; an older answer has neither', () => {
    expect(v(failed())).toMatchObject({ state: 'stop_failed', stopReason: 'processes_remain', actions: ['force_stop', 'mark_ended'] })
    expect(v(raw({ state: 'stop_failed' }))).toMatchObject({ stopReason: null, actions: ['force_stop'] })
    expect(v(failed({ stop_reason: '' })).stopReason).toBeNull()
    expect(v(failed({ stop_reason: 42 })).stopReason).toBeNull()
    // Carried on the run instead of the line: still read.
    expect(v(failed({ stop_reason: undefined, run: runRaw({ stop_reason: 'host_changed' }) })).stopReason).toBe('host_changed')
  })

  it('names the three reasons, any other code as unknown, and nothing outside 종료 실패', () => {
    expect(verificationStopReason(v(failed({ stop_reason: 'host_changed' })))).toBe('host_changed')
    expect(verificationStopReason(v(failed({ stop_reason: 'processes_remain' })))).toBe('processes_remain')
    expect(verificationStopReason(v(failed({ stop_reason: 'owner_alive' })))).toBe('owner_alive')
    expect(verificationStopReason(v(failed({ stop_reason: 'tree_unproven' })))).toBe('unknown')
    expect(verificationStopReason(v(failed({ stop_reason: null })))).toBeNull()
    expect(verificationStopReason(v(raw({ stop_reason: 'processes_remain' })))).toBeNull()
    expect(verificationStopReason(null)).toBeNull()
  })

  it('stop_failed buttons: [다시 종료] then [끝난 것으로 처리], each only when offered', () => {
    expect(verificationRowView(v(failed()), true)).toEqual({
      state: 'stop_failed', tone: 'danger', danger: true, focus: false, buttons: ['retry_stop', 'mark_ended'], reason: 'processes_remain',
    })
    expect(verificationRowView(v(failed({ actions: ['mark_ended'] })), true)?.buttons).toEqual(['mark_ended'])
    expect(verificationRowView(v(failed({ actions: ['force_stop'] })), true)?.buttons).toEqual(['retry_stop'])
    // mark_ended belongs to 종료 실패 only.
    expect(verificationRowView(v(raw({ actions: ['force_stop', 'mark_ended'] })), true)?.buttons).toEqual(['force_stop'])
    expect(BUTTON_ACTION.mark_ended).toBe('mark_ended')
  })
})

describe('the 종료 실패 line', () => {
  it('shows the reason beside the badge; a new [다시 종료] answer shows its own reason', async () => {
    api.read.mockResolvedValue(v(failed()))
    const w = await mountRow()
    expect(badge(w).text()).toBe(t(k('state.stop_failed')))
    expect(q(w, 'verification-reason').text()).toBe(t(k('reason.processes_remain')))
    expect(q(w, 'verification-reason').text()).toBe('남은 프로세스가 있음')
    // Rework: the reason is its own line under the badge (the row's detail line), not squeezed into the first row, and
    // the line is the stacked kind (a narrow list puts its buttons on a row of their own).
    expect(w.find('[data-test="verification-row"] [data-test="inspector-slot-detail"] [data-test="verification-reason"]').exists()).toBe(true)
    expect(w.find('[data-test="verification-row"] .inspector-slot-value [data-test="verification-reason"]').exists()).toBe(false)
    expect(q(w, 'verification-reason').attributes('title')).toBe('남은 프로세스가 있음')
    expect(q(w, 'verification-row').classes()).toContain('is-stacked')
    expect(buttons(w)).toEqual(['verification-retry_stop', 'verification-mark_ended'])
    expect(q(w, 'verification-mark_ended').text()).toBe('끝난 것으로 처리')
    expect(q(w, 'verification-mark_ended').attributes('title')).toBe(t(k('hint.mark_ended')))

    api.act.mockResolvedValue(v(failed({ stop_reason: 'host_changed' })))
    await q(w, 'verification-retry_stop').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenLastCalledWith(FLOW, 'gn_4', 'scr_1', 'force_stop')
    expect(q(w, 'verification-reason').text()).toBe('서버가 바뀌어 확인할 수 없음')

    api.act.mockResolvedValue(v(failed({ stop_reason: 'something_new' })))
    await q(w, 'verification-retry_stop').trigger('click')
    await flushPromises()
    expect(q(w, 'verification-reason').text()).toBe(t(k('reason.unknown')))

    // The answer without a reason (an older server) clears it.
    api.act.mockResolvedValue(v(raw({ state: 'stop_failed' })))
    await q(w, 'verification-retry_stop').trigger('click')
    await flushPromises()
    expect(q(w, 'verification-reason').exists()).toBe(false)
    expect(q(w, 'inspector-slot-detail').exists()).toBe(false)
    expect(buttons(w)).toEqual(['verification-retry_stop'])
  })

  it('[끝난 것으로 처리] asks in a dialog (cannot be undone, processes may remain); [취소] keeps the line', async () => {
    api.read.mockResolvedValue(v(failed({ stop_reason: 'owner_alive' })))
    const w = await mountRow()
    expect(q(w, 'verification-reason').text()).toBe('검증 작업이 아직 끝나지 않음')
    expect(dialog(w).props('open')).toBe(false)
    await q(w, 'verification-mark_ended').trigger('click')
    expect(api.act).not.toHaveBeenCalled()
    expect(dialog(w).props()).toMatchObject({
      open: true, danger: true, title: t(k('markEnded.title')), message: t(k('markEnded.message')),
      confirmLabel: t(k('button.mark_ended')), cancelLabel: t(k('dismiss')),
    })
    expect(t(k('markEnded.message'))).toContain('되돌릴 수 없습니다')
    expect(t(k('markEnded.message'))).toContain('남아 있을 수 있습니다')
    // Not an in-place [확인] [취소]: the line keeps its buttons under the dialog.
    expect(buttons(w)).toEqual(['verification-retry_stop', 'verification-mark_ended'])

    dialog(w).vm.$emit('cancel')
    await flushPromises()
    expect(dialog(w).props('open')).toBe(false)
    expect(api.act).not.toHaveBeenCalled()
  })

  it('confirming calls mark_ended for the run and shows 종료됨', async () => {
    api.read.mockResolvedValue(v(failed()))
    const w = await mountRow()
    api.act.mockResolvedValue(v(raw({ state: 'stopped', actions: [], run: runRaw({ status: 'cancelled', finished_at: '2026-11-19T08:50:00Z' }) })))
    await q(w, 'verification-mark_ended').trigger('click')
    dialog(w).vm.$emit('confirm')
    await flushPromises()
    expect(api.act).toHaveBeenCalledTimes(1)
    expect(api.act).toHaveBeenCalledWith(FLOW, 'gn_4', 'scr_1', 'mark_ended')
    expect(badge(w).text()).toBe(t(k('state.stopped')))
    expect(buttons(w)).toEqual([])
    expect(q(w, 'verification-reason').exists()).toBe(false)
    expect(dialog(w).props('open')).toBe(false)
  })

  it('a refused mark_ended reads the line again', async () => {
    api.read.mockResolvedValueOnce(v(failed())).mockResolvedValue(v(failed({ stop_reason: 'host_changed' })))
    api.act.mockRejectedValue({ response: { status: 409, data: { ok: false, error: { code: 'selfcheck_not_running', message: 'no' } } } })
    const w = await mountRow()
    await q(w, 'verification-mark_ended').trigger('click')
    dialog(w).vm.$emit('confirm')
    await flushPromises()
    expect(api.read).toHaveBeenCalledTimes(2)
    expect(q(w, 'verification-reason').text()).toBe(t(k('reason.host_changed')))
  })

  it('other states draw no reason even if one is sent', async () => {
    api.read.mockResolvedValue(v(raw({ stop_reason: 'processes_remain', actions: ['force_stop', 'mark_ended'] })))
    const w = await mountRow()
    expect(badge(w).text()).toBe(t(k('state.overdue')))
    expect(q(w, 'verification-reason').exists()).toBe(false)
    expect(q(w, 'inspector-slot-detail').exists()).toBe(false)
    expect(q(w, 'verification-row').classes()).not.toContain('is-stacked')
    expect(buttons(w)).toEqual(['verification-force_stop'])
  })

  it('English and Japanese words', async () => {
    api.read.mockResolvedValue(v(failed({ stop_reason: 'host_changed' })))
    i18n.global.locale.value = 'en'
    const en = await mountRow()
    expect(q(en, 'verification-reason').text()).toBe('Cannot check: the server changed')
    expect(q(en, 'verification-mark_ended').text()).toBe('Mark as ended')
    wrappers.splice(0).forEach((w) => w.unmount())
    resetVerificationAnswers()
    i18n.global.locale.value = 'ja'
    const ja = await mountRow()
    expect(q(ja, 'verification-reason').text()).toBe('サーバーが変わったため確認できません')
    expect(q(ja, 'verification-mark_ended').text()).toBe('終了扱いにする')
    for (const locale of ['ko', 'en', 'ja']) {
      i18n.global.locale.value = locale
      for (const key of ['reason.host_changed', 'reason.processes_remain', 'reason.owner_alive', 'reason.unknown', 'markEnded.title', 'markEnded.message', 'button.mark_ended', 'hint.mark_ended']) {
        expect(t(k(key))).not.toBe(k(key))
      }
    }
  })
})
