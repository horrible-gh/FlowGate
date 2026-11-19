/**
 * flowgate.v02test.0053 1-D — the work card's `검증` line (시안 v15): drawn only while the server says so (a run past
 * last launch + time limit + 1 minute, or this card's request waiting behind one); name · badge · buttons only, times in
 * the badge tooltip; [강제 종료] / [취소] confirm in place; [종료됨] / [취소됨] stay briefly and the next read hides them;
 * [종료 실패] offers [다시 종료]; [→] shows the card ahead; refusals read again; 403 hides the line without asking again.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import VerificationRow from '@/features/designer/inspector/features/verification/VerificationRow.vue'
import verificationRegistration from '@/features/designer/inspector/features/verification/register'
import {
  OPEN_CARD_EVENT,
  VERIFICATION_FEEDBACK_MS,
  VERIFICATION_IDLE_POLL_MS,
  VERIFICATION_SHOWN_POLL_MS,
  nextReadDelay,
  verificationRowView,
  verificationTimes,
  elapsedOf,
} from '@/features/designer/inspector/features/verification/verificationModel'
import { canGoTo, resetVerificationAnswers, useVerificationVisible } from '@/features/designer/inspector/features/verification/useVerification'
import { createInspectorRegistry, inspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import { toCardVerification } from '@/features/document/api/cardVerification'
import type { FlowGraph, FlowGraphNode, FlowGraphNodeRun, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import { defineComponent, effectScope, h, ref } from 'vue'
import { prepareInspectorI18n } from '../support/inspectorSupport'

const api = vi.hoisted(() => ({ read: vi.fn(), act: vi.fn() }))
vi.mock('@/features/document/api/cardVerification', async (original) => ({
  ...await original<typeof import('@/features/document/api/cardVerification')>(),
  cardVerificationRead: api.read,
  cardVerificationAct: api.act,
}))

const i18n = prepareInspectorI18n()
const t = (key: string, params: Record<string, unknown> = {}) => i18n.global.t(key, params)
const k = (key: string) => `gui.inspector.verification.${key}`
const FLOW = 'flowgate.v02test.0053'

function node(id: string, run: Partial<FlowGraphNodeRun> | null): FlowGraphNode {
  return {
    id, kind: 'task', title: '알림 큐 분리', state: 'in_progress', docType: 'T', x: 0, y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: 'doc_t', label: '알림 큐 분리', assignee: null, rootBound: false },
    document: null, slot: null,
    nodeRun: run ? { status: 'running', openedAt: null, finishedAt: null, resultDocId: null, attemptId: 'att_1', ...run } : null,
  }
}
function selection(run: Partial<FlowGraphNodeRun> | null = {}, id = 'gn_4'): FlowGraphSelection {
  const graph: FlowGraph = { flowId: FLOW, nodes: [node(id, run)], edges: [], ownership: 'graph', draft: null, viewMode: 'revision' }
  return { projectId: 'flowgate', graph, node: graph.nodes[0] }
}
const runRaw = (over: Record<string, unknown> = {}) => ({
  self_check_run_id: 'scr_1', status: 'running', started_at: '2026-11-19T08:42:00Z', enqueued_at: '2026-11-19T08:41:00Z',
  finished_at: null, show_at: '2026-11-19T08:46:00Z', elapsed_seconds: 300, ...over,
})
const raw = (over: Record<string, unknown> = {}) => ({
  flow_id: FLOW, node_key: 'gn_4', visible: true, state: 'overdue', run: runRaw(), blocker: null, actions: ['force_stop'], ...over,
})
const hidden = (over: Record<string, unknown> = {}) => raw({ visible: false, state: 'hidden', run: null, actions: [], ...over })
const waiting = (blocker: Record<string, unknown> = {}) => raw({
  state: 'waiting', run: runRaw({ self_check_run_id: 'scr_w', status: 'queued', started_at: null, show_at: null, elapsed_seconds: null }),
  blocker: { ...runRaw({ self_check_run_id: 'scr_h' }), flow_id: FLOW, node_key: 'gn_2', label: '3. 알림 큐 분리', project_id: 'flowgate', ...blocker },
  actions: ['cancel_waiting'],
})
const v = (body: Record<string, unknown>) => toCardVerification(body)

const wrappers: VueWrapper<any>[] = []
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
  resetVerificationAnswers()
  vi.useRealTimers()
  document.body.innerHTML = ''
})
beforeEach(() => {
  vi.clearAllMocks()
  i18n.global.locale.value = 'ko'
})

const SlotRows = defineComponent({ setup: () => () => h('div', ['지시서', '레포트'].map((name) => h(InspectorSlotRow, { name, state: { label: '승인됨', tone: 'success' } }))) })
const Memo = defineComponent({ setup: () => () => h('p', { 'data-test': 'memo-stub' }, 'memo') })
const fixture = createInspectorRegistry({
  './features/taskCard/register.ts': { default: { key: 'taskCard', order: 403, cardKinds: ['task'], requiresDocument: false, tab: { key: 'documents', component: SlotRows }, messages: { ko: {}, en: {}, ja: {} } } },
  './features/cardMemo/register.ts': { default: { key: 'cardMemo', order: 580, requiresDocument: false, tab: { key: 'documents', component: Memo }, messages: { ko: {}, en: {}, ja: {} } } },
  './features/verification/register.ts': { default: verificationRegistration },
})

async function mountRow(run: Partial<FlowGraphNodeRun> | null = {}, ownership = 'graph') {
  const picked = selection(run)
  ;(picked.graph as { ownership: string }).ownership = ownership
  const w = mount(DesignerInspector, { props: { selection: picked, registry: fixture }, attachTo: document.body, global: { plugins: [i18n] } })
  wrappers.push(w)
  await flushPromises()
  return w
}
/** One Frame at a time: the answers are shared per card. */
function unmountAll(): void {
  wrappers.splice(0).forEach((w) => w.unmount())
  resetVerificationAnswers()
}
const q = (w: VueWrapper<any>, test: string) => w.find(`[data-test="${test}"]`)
const badge = (w: VueWrapper<any>) => w.find('[data-test="verification-row"] [data-test="inspector-slot-state"]')
const buttons = (w: VueWrapper<any>) => w.findAll('[data-test="verification-row"] button:not([data-test="inspector-slot-name"])').map((b) => b.attributes('data-test'))
const sections = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-section"]').map((s) => s.attributes('data-key'))

describe('registration and answer', () => {
  it('a Graph work card line in the documents tab after the merge line', () => {
    const entry = inspectorRegistry.entries.find((item) => item.key === 'verification')!
    expect(entry).toBe(verificationRegistration)
    expect(entry.tab?.key).toBe('documents')
    expect(entry.tab?.component).toBe(VerificationRow)
    expect(entry.order).toBe(445)
    expect(entry.graphOnly).toBe(true)
    expect(entry.cardKinds).toEqual(['task'])
    expect(entry.ownerships).toEqual(['graph'])
    expect(entry.tab?.visible).toBe(useVerificationVisible)
    expect(entry.requiresDocument).toBe(false)
  })

  it('reads the answer; an unknown state or visible:false is hidden, unknown actions are dropped', () => {
    const shown = v(raw({ actions: ['force_stop', 'explode'] }))
    expect(shown).toMatchObject({ flowId: FLOW, nodeKey: 'gn_4', visible: true, state: 'overdue', actions: ['force_stop'] })
    expect(shown.run).toMatchObject({ runId: 'scr_1', status: 'running', elapsedSeconds: 300, showAt: '2026-11-19T08:46:00Z' })
    expect(v(raw({ state: 'weird' }))).toMatchObject({ visible: false, state: 'hidden' })
    expect(v(raw({ visible: false }))).toMatchObject({ visible: false, state: 'hidden' })
    expect(v(waiting()).blocker).toMatchObject({ runId: 'scr_h', flowId: FLOW, nodeKey: 'gn_2', label: '3. 알림 큐 분리', projectId: 'flowgate' })
    expect(v(waiting({ label: null })).blocker?.label).toBe('gn_2')
    expect(toCardVerification(null, FLOW, 'gn_4')).toMatchObject({ flowId: FLOW, nodeKey: 'gn_4', state: 'hidden', run: null })
  })

  it('the model: line, buttons, next read, tooltip times', () => {
    expect(verificationRowView(v(hidden()), true)).toBeNull()
    expect(verificationRowView(v(raw()), true)).toEqual({ state: 'overdue', tone: 'danger', danger: true, focus: false, buttons: ['force_stop'] })
    expect(verificationRowView(v(raw({ state: 'stop_failed' })), true)?.buttons).toEqual(['retry_stop'])
    expect(verificationRowView(v(waiting()), true)).toMatchObject({ tone: 'warning', focus: true, buttons: ['go', 'cancel_waiting'] })
    expect(verificationRowView(v(waiting()), false)?.buttons).toEqual(['cancel_waiting'])
    expect(verificationRowView(v(raw({ state: 'stopped', actions: [] })), true)).toMatchObject({ tone: 'success', buttons: [] })
    expect(verificationRowView(v(raw({ state: 'cancelled', actions: [] })), true)).toMatchObject({ tone: 'hold', buttons: [] })

    const now = Date.parse('2026-11-19T08:45:00Z')
    expect(nextReadDelay(v(raw()), now)).toBe(VERIFICATION_SHOWN_POLL_MS)
    expect(nextReadDelay(v(hidden()), now)).toBe(VERIFICATION_IDLE_POLL_MS)
    expect(nextReadDelay(v(hidden({ run: runRaw({ show_at: '2026-11-19T08:45:10Z' }) })), now)).toBe(10500)
    expect(nextReadDelay(v(hidden({ run: runRaw({ show_at: '2026-11-19T09:45:00Z' }) })), now)).toBe(VERIFICATION_IDLE_POLL_MS)
    expect(nextReadDelay(v(hidden({ run: runRaw({ show_at: '2026-11-19T08:40:00Z' }) })), now)).toBe(VERIFICATION_SHOWN_POLL_MS)
    expect(nextReadDelay(null, now)).toBe(VERIFICATION_IDLE_POLL_MS)

    expect(elapsedOf(300)).toBe('5:00')
    expect(elapsedOf(3725)).toBe('62:05')
    expect(elapsedOf(null)).toBeNull()
    expect(verificationTimes(v(raw()))).toMatch(/^\d{2}:\d{2} · 5:00$/)
    expect(verificationTimes(v(raw({ state: 'stopped', run: runRaw({ finished_at: '2026-11-19T08:48:00Z' }) })))).toMatch(/^\d{2}:\d{2}$/)
    expect(verificationTimes(v(waiting()))).toMatch(/· 5:00$/)
  })

  it('[→] only to a card of the same Flow or the open project', () => {
    const target = toInspectorTarget(selection())!
    expect(canGoTo(target, v(waiting()))).toBe(true)
    expect(canGoTo(target, v(waiting({ flow_id: 'flowgate.v02test.0099' })))).toBe(true)
    expect(canGoTo(target, v(waiting({ flow_id: 'other.flow', project_id: 'other' })))).toBe(false)
    expect(canGoTo(target, v(raw()))).toBe(false)
  })
})

describe('the line', () => {
  it('draws nothing while hidden', async () => {
    api.read.mockResolvedValue(v(hidden()))
    const w = await mountRow()
    expect(api.read).toHaveBeenCalledWith(FLOW, 'gn_4')
    expect(q(w, 'verification-row').exists()).toBe(false)
    // A hidden line leaves no section (no gap between the card's lines and its memos).
    expect(sections(w)).toEqual(['taskCard', 'cardMemo'])
  })

  it('a draft Flow runs nothing: no line and no read', async () => {
    api.read.mockResolvedValue(v(raw()))
    const w = await mountRow({}, 'unassigned')
    expect(api.read).not.toHaveBeenCalled()
    expect(sections(w)).toEqual(['taskCard', 'cardMemo'])
  })

  it('timed out: name, badge with times in its tooltip, [강제 종료] confirmed in place, then 종료됨 and hidden', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    api.read.mockResolvedValue(v(raw()))
    const w = await mountRow()
    const row = q(w, 'verification-row')
    expect(row.attributes('data-state')).toBe('overdue')
    expect(row.find('[data-test="inspector-slot-name"]').text()).toBe(t(k('name')))
    expect(badge(w).text()).toBe(t(k('state.overdue')))
    expect(badge(w).attributes('title')).toMatch(new RegExp(`^${t(k('state.overdue'))} · \\d{2}:\\d{2} · 5:00$`))
    expect(row.classes()).toContain('is-danger')
    // No sentence in the line: only name, badge and buttons.
    expect(row.find('[data-test="inspector-slot-summary"]').exists()).toBe(false)
    expect(buttons(w)).toEqual(['verification-force_stop'])
    expect(q(w, 'verification-force_stop').text()).toBe(t(k('button.force_stop')))
    expect(q(w, 'verification-force_stop').attributes('title')).toBe(t(k('hint.force_stop')))

    await q(w, 'verification-force_stop').trigger('click')
    expect(buttons(w)).toEqual(['verification-confirm', 'verification-dismiss'])
    expect(api.act).not.toHaveBeenCalled()
    await q(w, 'verification-dismiss').trigger('click')
    expect(buttons(w)).toEqual(['verification-force_stop'])

    api.act.mockResolvedValue(v(raw({ state: 'stopped', actions: [], run: runRaw({ status: 'cancelled', finished_at: '2026-11-19T08:48:00Z' }) })))
    await q(w, 'verification-force_stop').trigger('click')
    await q(w, 'verification-confirm').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenCalledWith(FLOW, 'gn_4', 'scr_1', 'force_stop')
    expect(badge(w).text()).toBe(t(k('state.stopped')))
    expect(buttons(w)).toEqual([])

    api.read.mockResolvedValue(v(hidden()))
    const reads = api.read.mock.calls.length
    await vi.advanceTimersByTimeAsync(VERIFICATION_FEEDBACK_MS)
    await flushPromises()
    expect(api.read.mock.calls.length).toBe(reads + 1)
    expect(q(w, 'verification-row').exists()).toBe(false)
  })

  it('a stop that leaves processes: 종료 실패 and [다시 종료] without asking again', async () => {
    api.read.mockResolvedValue(v(raw()))
    api.act.mockResolvedValue(v(raw({ state: 'stop_failed' })))
    const w = await mountRow()
    await q(w, 'verification-force_stop').trigger('click')
    await q(w, 'verification-confirm').trigger('click')
    await flushPromises()
    expect(badge(w).text()).toBe(t(k('state.stop_failed')))
    expect(buttons(w)).toEqual(['verification-retry_stop'])
    expect(q(w, 'verification-retry_stop').text()).toBe(t(k('button.retry_stop')))
    await q(w, 'verification-retry_stop').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenCalledTimes(2)
    expect(api.act).toHaveBeenLastCalledWith(FLOW, 'gn_4', 'scr_1', 'force_stop')
  })

  it('waiting: [→] selects the card ahead in the same Flow, [취소] confirmed cancels this card\'s own request', async () => {
    api.read.mockResolvedValue(v(waiting()))
    const w = await mountRow()
    expect(badge(w).text()).toBe(t(k('state.waiting')))
    expect(badge(w).attributes('title')).toContain(t(k('ahead'), { label: '3. 알림 큐 분리' }))
    expect(q(w, 'verification-row').classes()).toContain('is-focus')
    expect(buttons(w)).toEqual(['verification-go', 'verification-cancel_waiting'])
    expect(q(w, 'verification-go').attributes('aria-label')).toBe(t(k('go'), { label: '3. 알림 큐 분리' }))
    await q(w, 'verification-go').trigger('click')
    expect(w.emitted('host')).toContainEqual([{ kind: 'select-node', nodeId: 'gn_2' }])

    api.act.mockResolvedValue(v(raw({ state: 'cancelled', actions: [], run: runRaw({ self_check_run_id: 'scr_w', status: 'cancelled' }) })))
    await q(w, 'verification-cancel_waiting').trigger('click')
    expect(buttons(w)).toEqual(['verification-confirm', 'verification-dismiss'])
    await q(w, 'verification-confirm').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenCalledWith(FLOW, 'gn_4', 'scr_w', 'cancel_waiting')
    expect(badge(w).text()).toBe(t(k('state.cancelled')))
  })

  it('[→] to another Flow of the project asks the Designer; another project has no [→]', async () => {
    const heard = vi.fn()
    window.addEventListener(OPEN_CARD_EVENT, heard)
    api.read.mockResolvedValue(v(waiting({ flow_id: 'flowgate.v02test.0099' })))
    const w = await mountRow()
    await q(w, 'verification-go').trigger('click')
    expect(w.emitted('host') ?? []).not.toContainEqual([expect.objectContaining({ kind: 'select-node' })])
    expect(heard).toHaveBeenCalledTimes(1)
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toEqual({ projectId: 'flowgate', flowId: 'flowgate.v02test.0099', nodeKey: 'gn_2' })
    window.removeEventListener(OPEN_CARD_EVENT, heard)

    unmountAll()
    api.read.mockResolvedValue(v(waiting({ flow_id: 'x.flow', project_id: 'x' })))
    const other = await mountRow()
    expect(buttons(other)).toEqual(['verification-cancel_waiting'])
    unmountAll()
    api.read.mockResolvedValue(v({ ...waiting(), blocker: null }))
    const unseen = await mountRow()
    expect(buttons(unseen)).toEqual(['verification-cancel_waiting'])
  })

  it('a cancel refused because the request started reads the line again', async () => {
    api.read.mockResolvedValueOnce(v(waiting())).mockResolvedValue(v(raw()))
    api.act.mockRejectedValue({ response: { status: 409, data: { ok: false, error: { code: 'selfcheck_not_waiting', message: 'started' } } } })
    const w = await mountRow()
    await q(w, 'verification-cancel_waiting').trigger('click')
    await q(w, 'verification-confirm').trigger('click')
    await flushPromises()
    expect(api.read).toHaveBeenCalledTimes(2)
    expect(q(w, 'verification-row').attributes('data-state')).toBe('overdue')
    expect(buttons(w)).toEqual(['verification-force_stop'])
  })

  it('reads again at the moment the line would appear; 403 hides it and stops asking', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const showAt = new Date(Date.now() + 10_000).toISOString()
    api.read.mockResolvedValueOnce(v(hidden({ run: runRaw({ show_at: showAt }) }))).mockResolvedValue(v(raw()))
    const w = await mountRow()
    expect(q(w, 'verification-row').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(9_000)
    expect(api.read).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2_000)
    await flushPromises()
    expect(api.read).toHaveBeenCalledTimes(2)
    expect(q(w, 'verification-row').exists()).toBe(true)

    api.read.mockRejectedValue({ response: { status: 403, data: { ok: false, error: { code: 'flow_access_denied', message: 'no' } } } })
    await vi.advanceTimersByTimeAsync(VERIFICATION_SHOWN_POLL_MS)
    await flushPromises()
    expect(q(w, 'verification-row').exists()).toBe(false)
    const reads = api.read.mock.calls.length
    await vi.advanceTimersByTimeAsync(VERIFICATION_IDLE_POLL_MS * 2)
    expect(api.read.mock.calls.length).toBe(reads)
  })

  it('stops reading once the line is gone', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    api.read.mockResolvedValue(v(raw()))
    const w = await mountRow()
    w.unmount()
    wrappers.splice(wrappers.indexOf(w), 1)
    expect(api.read).toHaveBeenCalled()
    const reads = api.read.mock.calls.length
    await vi.advanceTimersByTimeAsync(VERIFICATION_IDLE_POLL_MS)
    expect(api.read.mock.calls.length).toBe(reads)
  })

  it('Japanese and English labels', async () => {
    api.read.mockResolvedValue(v(waiting()))
    i18n.global.locale.value = 'ja'
    const ja = await mountRow()
    expect(q(ja, 'verification-row').find('[data-test="inspector-slot-name"]').text()).toBe('検証')
    expect(badge(ja).text()).toBe('待機中')
    expect(q(ja, 'verification-cancel_waiting').text()).toBe('キャンセル')
    await q(ja, 'verification-cancel_waiting').trigger('click')
    expect(q(ja, 'verification-confirm').text()).toBe('確認')
    expect(q(ja, 'verification-dismiss').text()).toBe('戻る')
    unmountAll()
    i18n.global.locale.value = 'en'
    api.read.mockResolvedValue(v(raw()))
    const en = await mountRow()
    expect(badge(en).text()).toBe('Timed out')
    expect(q(en, 'verification-force_stop').text()).toBe('Force stop')
  })
})

describe('in the Inspector', () => {
  it('the line follows the 지시서 · 레포트 lines, before the memos', async () => {
    api.read.mockResolvedValue(v(raw()))
    const w = await mountRow()
    expect(sections(w)).toEqual(['taskCard', 'verification', 'cardMemo'])
    const names = w.findAll('[data-test="inspector-slot-name"]').map((n) => n.text())
    expect(names).toEqual(['지시서', '레포트', t(k('name'))])
  })

  it('the supplier stops reading when its scope ends or the registration no longer applies', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    api.read.mockResolvedValue(v(hidden()))
    const target = ref(toInspectorTarget(selection()))
    const scope = effectScope()
    const visible = scope.run(() => useVerificationVisible(target))!
    await flushPromises()
    expect(api.read).toHaveBeenCalledTimes(1)
    expect(visible.value).toBe(false)
    api.read.mockResolvedValue(v(raw()))
    await vi.advanceTimersByTimeAsync(VERIFICATION_IDLE_POLL_MS)
    await flushPromises()
    expect(visible.value).toBe(true)
    target.value = null
    await flushPromises()
    expect(visible.value).toBe(false)
    const reads = api.read.mock.calls.length
    await vi.advanceTimersByTimeAsync(VERIFICATION_IDLE_POLL_MS * 2)
    expect(api.read.mock.calls.length).toBe(reads)
    scope.stop()
  })
})
