import { defineComponent, h, shallowRef, watchEffect } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReportWindow from '@/features/designer/inspector/features/testSpec/windows/ReportWindow.vue'
import FollowWindow from '@/features/designer/inspector/features/testSpec/windows/FollowWindow.vue'
import TestSpecTab from '@/features/designer/inspector/features/testSpec/TestSpecTab.vue'
import reportSource from '@/features/designer/inspector/features/testSpec/windows/ReportWindow.vue?raw'
import followSource from '@/features/designer/inspector/features/testSpec/windows/FollowWindow.vue?raw'
import { reportDefaultFilter, reportDefects, reportGate, reportItems, reportKeyAction, reportLegend, reportMode, reportMove, reportNextPending,
  reportPrimary, reportReasonReady, reportRerunOutcome, reportRunNumber, reportSelection, reportTagKey, reportVisible, type ReportDecision, type ReportKeyFacts } from '@/features/designer/inspector/features/testSpec/windows/reportWindowModel'
import { documentHistoryWhen } from '@/features/documentWindow'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { testCardPresentation } from '@/features/designer/inspector/features/testSpec/testSpecModel'
import { useTestCard, type TestCardContext } from '@/features/designer/inspector/features/testSpec/useTestCard'
import { testCardSessionKey } from '@/features/designer/inspector/features/testSpec/testCardSession'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n, targetOf } from '../../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from '../../support/projectEventStream'
import { TS_DOC, TS_NODE, counts, liveRun, runRow, testSelection, tsDocument } from '../testSpecFixtures'

const mocks = vi.hoisted(() => ({ read: vi.fn(), node: vi.fn(), choices: vi.fn(), setting: vi.fn(), guard: vi.fn(), activity: vi.fn(), retry: vi.fn(), enter: vi.fn(), history: vi.fn() }))
vi.mock('@/features/document', async (original) => {
  const actual = await original<typeof import('@/features/document')>()
  return { ...actual, testSpecRead: mocks.read, settingsNodeRead: mocks.node, settingsChoicesRead: mocks.choices, settingsNodeWrite: mocks.setting,
    reviewDocumentRead: mocks.guard, reviewActivityRead: mocks.activity, testSpecCaseRun: mocks.retry, testSpecResultEnter: mocks.enter, documentHistoryRead: mocks.history }
})
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => { throw new Error('offline') }) }))
const { toTestSpecView, toReviewDocument, toReviewActivity, toSettingsNode, toSettingsChoices, toDocumentHistory } = await vi.importActual<typeof import('@/features/document')>('@/features/document')
const i18n = prepareInspectorI18n()
const base = targetOf(testSelection('graph', TS_NODE))
const makeTarget = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...base, nodeId: 'gn_a', nodeType: 'document', cardDocType: 'TS', docId: null, docType: 'TS',
  cardDocuments: [{ slot: 'scenario', docType: 'TS', docId: TS_DOC, title: 'Scenario', author: 'human', status: 'open', docReviewStatus: 'approved' }], ...fields })
const CASES = [
  { case_id: 'TC-1', title: 'Expiry notice', category: 'normal', requirement: 'R §1', execution_mode: 'automated', required: true, procedure: 'Run', expected: 'Notice shown', check_points: 'c', automation_ref: 'tests/test_auth.py::test_1' },
  { case_id: 'TC-2', title: 'Other device', category: 'normal', requirement: 'R §2', execution_mode: 'automated', required: true, procedure: 'Run', expected: 'Alert', check_points: 'c', automation_ref: 'tests/test_auth.py::test_2' },
  { case_id: 'TC-3', title: 'Mobile layout', category: 'normal', requirement: 'R §3', execution_mode: 'manual', required: false, procedure: 'Look', expected: 'Fits', check_points: 'c' },
]
const ROWS = [
  { case_id: 'TC-1', status: 'FAIL', actual: 'No notice', evidence: [{ kind: 'log', value: 'log_tc1.txt' }] },
  { case_id: 'TC-2', status: 'BLOCKED', actual: 'Server timeout', evidence: [] },
]
const confirmServer = () => tsDocument({ cases: CASES, effective_result: { summary: { overall: 'FAIL', counts: counts(0, 1, 1, 1) }, cases: ROWS },
  latest_result: runRow('tr_2', { overall: 'FAIL', summary: { counts: counts(0, 1, 1, 1) }, created_at: '2026-10-10T10:00:00Z', finished_at: '2026-10-10T10:02:00Z' }),
  last_execution: runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }), run_history: [runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }), runRow('tr_1')] })
const completeServer = () => tsDocument({ cases: CASES, effective_result: { summary: { overall: 'PASS', counts: counts(3, 0) },
  cases: [{ case_id: 'TC-1', status: 'PASS', actual: 'ok', evidence: [{ kind: 'log', value: 'ok.txt' }] }, { case_id: 'TC-2', status: 'PASS' }, { case_id: 'TC-3', status: 'PASS' }] } })
// The stored record as describe_test_document shapes it (case_no / case_title / case_status / expect …), for the previous-result cases.
const STORED = [
  { case_no: 'TC-1', case_title: 'Old expiry', case_status: 'FAIL', expect: 'Old notice', actual: 'Old failure', evidence: [{ kind: 'log', value: 'old.log' }], required: true, execution_mode: 'automated' },
  { case_no: 'TC-2', case_title: 'Other device', case_status: 'PASS', expect: 'Alert', actual: 'ok', evidence: [], required: true, execution_mode: 'automated' },
  { case_no: 'TC-3', case_title: 'Old deleted case', case_status: 'NOT_RUN', expect: 'Gone', required: false, execution_mode: 'manual' },
]
/** Stale basis and no valid run: effective_result is the NOT_RUN mapping of the current cases (server map_results(cases, [])). */
const staleServer = (fields: Record<string, unknown> = {}) => {
  const cases = (fields.cases as typeof CASES | undefined) ?? CASES
  return tsDocument({ cases, basis_valid: false, basis_verdict: { state: 'stale', reasons: ['source_changed'] },
    effective_result: { summary: { overall: 'NOT_RUN', counts: counts(0, 0, 0, cases.length) }, cases: cases.map(item => ({ case_id: item.case_id, title: item.title, status: 'NOT_RUN' })) },
    latest_result: runRow('tr_2', { overall: 'FAIL', summary: { counts: counts(1, 1, 0, 1) }, created_at: '2026-10-10T10:00:00Z', finished_at: '2026-10-10T10:02:00Z', cases: STORED }),
    last_execution: runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }), run_history: [runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }), runRow('tr_1')], ...fields })
}
/** TC-1 · TC-2 failed (both rerunnable one by one), TC-3 is a human check without a result: three confirmation targets. */
const RERUN_ROWS = [{ case_id: 'TC-1', status: 'FAIL', actual: 'No notice', evidence: [] }, { case_id: 'TC-2', status: 'FAIL', actual: 'No alert', evidence: [] }]
const rerunServer = () => tsDocument({ cases: CASES, case_capabilities: { 'TC-1': 'case_selectable', 'TC-2': 'case_selectable', 'TC-3': 'none' },
  effective_result: { summary: { overall: 'FAIL', counts: counts(0, 2, 0, 1) }, cases: RERUN_ROWS },
  latest_result: runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }),
  last_execution: runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }), run_history: [runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }), runRow('tr_1')] })

let wrapper: VueWrapper | null = null, context: TestCardContext
const contextRef = shallowRef<TestCardContext | null>(null)
let selected = shallowRef(makeTarget())
let server: Record<string, unknown>, stored: Record<string, unknown>, causeMode: string
const Probe = defineComponent({ setup() { const card = useTestCard(() => selected.value); watchEffect(() => { if (card.value) { context = card.value; contextRef.value = card.value } }); return () => h('span') } })
async function render(kinds: readonly ('report' | 'follow')[] = ['report'], caseId: string | null = null, stubTeleport = true) {
  const components = { report: ReportWindow, follow: FollowWindow }
  wrapper = mount(defineComponent({ setup: () => () => h('div', [h(Probe), ...(['report', 'follow'] as const).map(kind =>
    contextRef.value?.windows[kind].open ? h(components[kind], { key: `${testCardSessionKey(selected.value)}:${kind}`, target: selected.value }) : null)]) }),
  { global: { plugins: [i18n, createPinia()], stubs: { teleport: stubTeleport } }, attachTo: document.body })
  await flushPromises()
  for (const kind of kinds) context.openWindow(kind, kind === 'report' ? caseId : null)
  await flushPromises()
  return wrapper
}
const settingsNode = () => toSettingsNode({ flow_id: base.flowId, node_key: 'gn_a', card_kind: 'test', locked: false, fields: {
  instruction: { value: 'use', choices: ['use', 'omit'] }, result_cause: { value: stored.result_cause ?? causeMode, default: 'ai', choices: ['ai', 'human'] },
  follow_scope: { value: stored.follow_scope ?? 'summary', default: 'summary', choices: ['summary', 'full'] } } })
const key = (keyName: string, target: EventTarget = document.body) => target.dispatchEvent(new KeyboardEvent('keydown', { key: keyName, bubbles: true, cancelable: true }))
const detailCase = (w: VueWrapper) => w.get('[data-test="report-detail"]').attributes('data-case')

beforeEach(() => {
  Object.values(mocks).forEach(mock => mock.mockReset())
  context = undefined as unknown as TestCardContext; contextRef.value = null
  i18n.global.locale.value = 'ko'; selected = shallowRef(makeTarget())
  server = confirmServer(); stored = {}; causeMode = 'ai'
  mocks.read.mockImplementation(async () => toTestSpecView(server))
  mocks.node.mockImplementation(async () => settingsNode())
  mocks.choices.mockResolvedValue(toSettingsChoices({ providers: [] }))
  mocks.setting.mockImplementation(async (_flow: string, _key: string, field: string, value: unknown) => { stored[field] = value; return settingsNode() })
  mocks.guard.mockImplementation(async (id: string) => toReviewDocument({ doc_id: id, is_editable: true, group_disposed: false }))
  mocks.activity.mockImplementation(async () => toReviewActivity({ active: false }))
  mocks.history.mockResolvedValue(toDocumentHistory({ doc_id: TS_DOC }))
  mocks.retry.mockResolvedValue({ runId: 'retry', status: 'running' })
  mocks.enter.mockResolvedValue(undefined)
})
afterEach(() => { wrapper?.unmount(); wrapper = null; document.body.innerHTML = '' })

describe('0071 T3 report rules', () => {
  const facts = () => {
    const view = toTestSpecView(server)
    return { view, cases: view.details!.cases, rows: view.details!.rows, presentation: testCardPresentation(view, null, view.details!.rows) }
  }
  it('lists confirmation targets, the gate and the default filter of each mode', () => {
    const { view, cases, rows, presentation } = facts()
    expect(presentation.stage).toBe(5); expect(reportMode(presentation)).toBe('confirm')
    const items = reportItems(view, cases, rows, presentation)
    expect(items.map(item => [item.caseId, item.state, item.pending, item.manualCheck])).toEqual([['TC-1', 'fail', true, false], ['TC-2', 'blocked', true, false], ['TC-3', 'not_run', true, true]])
    expect(reportGate(items).map(item => item.caseId)).toEqual(['TC-1', 'TC-2'])
    expect(reportLegend(items)).toMatchObject({ pass: 0, fail: 1, blocked: 1, not_run: 1 })
    expect(reportDefaultFilter('confirm', items)).toBe('flagged'); expect(reportDefaultFilter('complete', [])).toBe('all')
    expect(reportTagKey('previous', 'cancelled')).toBe('tag_previous_cancelled'); expect(reportTagKey('confirm', 'done')).toBe('tag_confirm')
  })
  it('splits decided cases into implementation defects and the rest, and decided cases leave the pending list', () => {
    const { view, cases, rows, presentation } = facts()
    const decisions: ReportDecision[] = [{ caseId: 'TC-1', cause: 'impl', reason: null, by: 'a', at: null }, { caseId: 'TC-2', cause: 'pass', reason: 'flaky server', by: 'a', at: null }, { caseId: 'TC-3', cause: 'manualF', reason: null, by: 'a', at: null }]
    const items = reportItems(view, cases, rows, presentation, decisions)
    expect(items.every(item => !item.pending)).toBe(true)
    const { defects, others } = reportDefects(items)
    expect(defects.map(item => item.caseId)).toEqual(['TC-1', 'TC-3']); expect(others.map(item => item.caseId)).toEqual(['TC-2'])
    expect(reportVisible(items, 'complete', 'flagged')).toHaveLength(3)
  })
  it('J/K clamp in the listed cases and the next pending case goes once around from the current one', () => {
    const { view, cases, rows, presentation } = facts()
    const items = reportItems(view, cases, rows, presentation)
    expect(reportMove(items, 'TC-1', 1)).toBe('TC-2'); expect(reportMove(items, 'TC-3', 1)).toBe('TC-3'); expect(reportMove(items, 'TC-1', -1)).toBe('TC-1')
    expect(reportMove(items, 'missing', 1)).toBe('TC-1'); expect(reportMove([], null, 1)).toBeNull()
    expect(reportNextPending(items, 'TC-3')).toBe('TC-1'); expect(reportNextPending(items, 'TC-1')).toBe('TC-2')
    expect(reportSelection(items, 'gone')).toBe('TC-1')
  })
  it('the primary action is rerun for an environment cause, confirm otherwise, none for human checks, human mode and retries', () => {
    const { view, cases, rows, presentation } = facts()
    const [failed, , manual] = reportItems(view, cases, rows, presentation)
    expect(reportPrimary(failed, 'ai', 'env')).toBe('rerun'); expect(reportPrimary(failed, 'ai', null)).toBe('confirm')
    expect(reportPrimary(failed, 'human', 'env')).toBeNull(); expect(reportPrimary(manual, 'ai', null)).toBeNull()
    expect(reportPrimary({ ...failed, retrying: true }, 'ai', 'env')).toBeNull()
  })
  it('the keyboard works only in an open, restored window and never while typing or inside another window', () => {
    const on: ReportKeyFacts = { key: 'j', open: true, minimized: false, modifier: false, typing: false, otherWindow: false, onButton: false }
    expect(reportKeyAction(on)).toBe('next'); expect(reportKeyAction({ ...on, key: 'ArrowUp' })).toBe('prev'); expect(reportKeyAction({ ...on, key: 'Enter' })).toBe('primary')
    expect(reportKeyAction({ ...on, typing: true })).toBeNull(); expect(reportKeyAction({ ...on, minimized: true })).toBeNull()
    expect(reportKeyAction({ ...on, open: false })).toBeNull(); expect(reportKeyAction({ ...on, otherWindow: true })).toBeNull()
    expect(reportKeyAction({ ...on, key: 'Enter', onButton: true })).toBeNull(); expect(reportKeyAction({ ...on, key: 'Escape', typing: true })).toBe('escape')
  })
  it('a blank reason is not ready', () => {
    expect(reportReasonReady('')).toBe(false); expect(reportReasonReady('   ')).toBe(false); expect(reportReasonReady(' flaky ')).toBe(true)
  })
  it('a previous result is its stored record: stale effective NOT_RUN rows and the current scenario are not mixed in', () => {
    // describe_test_document on a stale basis without a valid run: effective NOT_RUN rows, the stored record keeps its FAIL.
    server = staleServer()
    const view = toTestSpecView(server)
    const presentation = testCardPresentation(view, null, view.details!.rows)
    expect(presentation.stage).toBe(3); expect(reportMode(presentation)).toBe('previous')
    expect(view.details!.rows.map(row => row.status)).toEqual(['NOT_RUN', 'NOT_RUN', 'NOT_RUN'])
    const items = reportItems(view, view.details!.cases, view.details!.rows, presentation)
    expect(items.map(item => [item.caseId, item.state, item.actual])).toEqual([['TC-1', 'fail', 'Old failure'], ['TC-2', 'pass', 'ok'], ['TC-3', 'not_run', null]])
    expect(items[0].evidence.map(item => item.value)).toEqual(['old.log'])
    expect(reportGate(items).map(item => item.caseId)).toEqual(['TC-1'])
    expect(items.some(item => item.flagged || item.pending)).toBe(false)
  })
  it('after a rewrite the previous result keeps deleted cases and its own fields for a reused ID', () => {
    server = staleServer({ cases: [{ ...CASES[0], title: 'New expiry', requirement: 'R §9', required: false, automation_ref: 'tests/new.py::test_9', expected: 'New notice' }] })
    const view = toTestSpecView(server)
    const items = reportItems(view, view.details!.cases, view.details!.rows, testCardPresentation(view, null, view.details!.rows))
    expect(items.map(item => item.caseId)).toEqual(['TC-1', 'TC-2', 'TC-3'])
    expect(items[0]).toMatchObject({ title: 'Old expiry', expected: 'Old notice', required: true, requirement: null, runCommand: null, status: 'FAIL' })
  })
  it('a full run shows no earlier verdict, actual or evidence as its own', () => {
    server.active_run = liveRun('run-all', { selected_case_ids: ['TC-1', 'TC-2'], reported_case_ids: [], created_at: '2026-10-11T10:00:00Z' })
    const view = toTestSpecView(server)
    const items = reportItems(view, view.details!.cases, view.details!.rows, testCardPresentation(view, null, view.details!.rows))
    expect(items.map(item => [item.state, item.status, item.actual, item.evidence.length])).toEqual([['running', null, null, 0], ['running', null, null, 0], ['queued', null, null, 0]])
  })
  it('a rerun is judged when it ends and the shown run is numbered by its own place in the run list', () => {
    const { view, cases, rows, presentation } = facts()
    const items = reportItems(view, cases, rows, presentation)
    expect(reportRerunOutcome(items, 'TC-1', true)).toBe('running')
    expect(reportRerunOutcome(items.map(item => item.caseId === 'TC-1' ? { ...item, retrying: true } : item), 'TC-1', false)).toBe('running')
    expect(reportRerunOutcome(items, 'TC-1', false)).toBe('kept')
    expect(reportRerunOutcome(items.map(item => item.caseId === 'TC-1' ? { ...item, status: 'PASS', state: 'pass' as const, flagged: false, pending: false } : item), 'TC-1', false)).toBe('passed')
    const history = [{ runId: 'tr_3' }, { runId: 'tr_2' }, { runId: 'tr_1' }]
    expect(reportRunNumber(history, 'tr_2')).toBe(2); expect(reportRunNumber(history, 'tr_3')).toBe(3); expect(reportRunNumber(history, 'gone')).toBeNull()
  })
})

describe('0071 T3 report window', () => {
  it('renders the result bar, legend, gate and the confirmation list with the first pending case selected', async () => {
    const w = await render()
    expect(w.get('[data-test="report-tag"]').text()).toBe('확인 필요 3')
    expect(w.get('[data-test="report-big"]').text()).toContain('3')
    expect(w.findAll('[data-test="report-cell"]').map(cell => cell.attributes('data-state'))).toEqual(['fail', 'blocked', 'not_run'])
    expect(w.get('[data-test="report-gate"]').text()).toContain('다음 단계 중단')
    expect(w.findAll('[data-test="report-gate-case"]').map(item => item.text())).toEqual(['TC-1', 'TC-2'])
    expect(w.get('[data-test="report-filter"] [aria-checked="true"]').text()).toContain('확인 필요')
    expect(detailCase(w)).toBe('TC-1'); expect(w.get('[data-test="report-actual"]').text()).toContain('No notice')
    expect(w.get('[data-test="report-command"]').text()).toBe('—'); expect(w.get('[data-test="report-detail"]').text()).not.toContain('실행 대상'); expect(w.get('[data-test="report-evidence"]').text()).toContain('log_tc1.txt')
    await w.findAll('[data-test="report-gate-case"]')[1].trigger('click')
    expect(detailCase(w)).toBe('TC-2')
  })
  it('J/K move inside the current list, and keys are ignored while minimized', async () => {
    const w = await render()
    key('j'); await flushPromises(); expect(detailCase(w)).toBe('TC-2')
    key('j'); key('j'); await flushPromises(); expect(detailCase(w)).toBe('TC-3')
    key('k'); await flushPromises(); expect(detailCase(w)).toBe('TC-2')
    context.windows.report.minimized = true; await flushPromises()
    key('k'); context.windows.report.minimized = false; await flushPromises(); expect(detailCase(w)).toBe('TC-2')
  })
  it('Enter sends nothing while its primary action is disabled (AI confirmation is not connected), nor from a focused button', async () => {
    const w = await render()
    expect(w.find('[data-test="report-confirm"]').exists()).toBe(false)
    expect(w.text()).not.toContain('연결 예정')
    key('Enter'); await flushPromises()
    key('Enter', w.get('[data-test="report-runs"]').element); await flushPromises()
    expect(mocks.retry).not.toHaveBeenCalled(); expect(mocks.enter).not.toHaveBeenCalled(); expect(mocks.setting).not.toHaveBeenCalled()
  })
  it('hides unimplemented decisions and keeps the implemented retry in human mode', async () => {
    causeMode = 'human'
    const w = await render()
    const options = w.findAll('[data-test="report-causes"] [data-test="inspector-segment-option"]')
    expect(options.map(option => option.text())).toEqual(['환경 문제'])
    expect(options[0].attributes('disabled')).toBeUndefined()
    for (const name of ['report-confirm', 'report-reopen', 'report-reason', 'report-record']) {
      expect(w.find(`[data-test="${name}"]`).exists()).toBe(false)
    }
    expect(w.text()).not.toContain('연결 예정')
  })
  it('환경 문제 reruns a case_selectable case through the session; a suite_only case stays disabled and sends nothing', async () => {
    const w = await render()
    await w.get('[data-test="report-other"]').trigger('click')
    await w.get('[data-test="report-cause"][data-cause="env"]').trigger('click'); await flushPromises()
    expect(mocks.retry).toHaveBeenCalledWith(TS_DOC, 'TC-1')
    mocks.retry.mockClear()
    await w.findAll('[data-test="report-row"]')[1].trigger('click')
    await w.get('[data-test="report-other"]').trigger('click')
    const env = w.get('[data-test="report-cause"][data-cause="env"]')
    expect(env.attributes('disabled')).toBeDefined(); expect(env.attributes('title')).toBe('이 케이스는 개별 코드 실행을 지원하지 않습니다')
    await env.trigger('click'); await flushPromises()
    expect(mocks.retry).not.toHaveBeenCalled()
  })
  it('a retried case shows its rerun in progress and keeps the stage', async () => {
    server.active_run = liveRun('retry', { selected_case_ids: ['TC-1'], reported_case_ids: [], created_at: '2026-10-11T10:00:00Z' })
    const w = await render()
    expect(context.presentation.value.stage).toBe(5)
    expect(w.get('[data-test="report-decision"]').text()).toContain('이 케이스 재실행 중')
  })
  it('a human check passes through the existing result path and selects the next pending case', async () => {
    mocks.enter.mockImplementation(async () => { server.effective_result = { summary: { overall: 'FAIL', counts: counts(1, 1, 1) }, cases: [...ROWS, { case_id: 'TC-3', status: 'PASS' }] } })
    const w = await render(['report'], 'TC-3')
    expect(detailCase(w)).toBe('TC-3'); expect(w.find('[data-test="report-attach"]').exists()).toBe(false)
    await w.get('[data-test="report-manual-pass"]').trigger('click'); await flushPromises()
    expect(mocks.enter).toHaveBeenCalledWith(TS_DOC, { case_id: 'TC-3', status: 'PASS' })
    expect(detailCase(w)).toBe('TC-1')
  })
  it('a previous result after a cancelled run is read only: no decision area and no follow-up', async () => {
    server.last_execution = runRow('tr_3', { status: 'cancelled', overall: null, created_at: '2026-10-11T10:00:00Z' })
    const w = await render()
    expect(context.presentation.value.stage).toBe(3)
    expect(w.get('[data-test="report-tag"]').text()).toBe('이전 결과 · 마지막 실행 취소됨')
    expect(w.find('[data-test="report-read-only"]').exists()).toBe(true)
    expect(w.find('[data-test="report-decision"]').exists()).toBe(false); expect(w.find('[data-test="report-follow"]').exists()).toBe(false)
    expect(context.openWindow('follow')).toBe(false)
  })
  it('a full run shows progress without gate or decisions', async () => {
    server.active_run = liveRun('run-all', { selected_case_ids: ['TC-1', 'TC-2'], reported_case_ids: ['TC-1'], created_at: '2026-10-11T10:00:00Z' })
    const w = await render()
    expect(w.get('[data-test="report-tag"]').text()).toBe('실행 중 1/2')
    expect(w.find('[data-test="report-gate"]').exists()).toBe(false); expect(w.find('[data-test="report-decision"]').exists()).toBe(false)
    expect(w.findAll('[data-test="report-cell"]').map(cell => cell.attributes('data-state'))).toEqual(['running', 'running', 'queued'])
  })
  it('uses theme tokens only: no fixed white backgrounds or light color scheme', () => {
    for (const source of [reportSource, followSource]) expect(source).not.toMatch(/#fff\b|#ffffff|:\s*white\b|color-scheme/i)
  })
})

describe('0071 T3 follow-up window', () => {
  it('opens from the confirmed report window and shows the result line and scope', async () => {
    server = completeServer()
    const w = await render()
    expect(w.get('[data-test="report-tag"]').text()).toBe('확인 완료')
    await w.get('[data-test="report-follow"]').trigger('click'); await flushPromises()
    expect(context.windows.follow.open).toBe(true)
    expect(w.get('[data-test="follow-result"]').text()).toContain('통과 3 · 실패 0 · 막힘 0 · 미실행 0')
    expect(w.get('[data-test="follow-scope"] [aria-checked="true"]').text()).toBe('결함 요약')
    expect(w.find('[data-test="follow-empty"]').exists()).toBe(true)
  })
  it('a scope change writes the shared card setting and the session holds the new value', async () => {
    server = completeServer()
    const w = await render(['follow'])
    await w.get('[data-test="follow-scope"] [data-value="full"]').trigger('click'); await flushPromises()
    expect(mocks.setting).toHaveBeenCalledWith(base.flowId, 'gn_a', 'follow_scope', 'full')
    expect(context.settings.node.value?.fields.follow_scope.value).toBe('full')
    expect(w.get('[data-test="follow-scope"] [aria-checked="true"]').text()).toBe('요약 + 로그 · 증거')
  })
  it('a refused scope change returns to the server value and shows the failure icon', async () => {
    server = completeServer()
    mocks.setting.mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { detail: { code: 'conflict', message: 'Server says no' } } } })
    const w = await render(['follow'])
    await w.get('[data-test="follow-scope"] [data-value="full"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="follow-scope"] [aria-checked="true"]').text()).toBe('결함 요약')
    expect(w.get('[data-test="follow-scope-failure"]').attributes('title')).toContain('범위를 저장하지 못했습니다')
  })
  it('a read-only card keeps the scope locked with its reason and sends nothing', async () => {
    server = completeServer(); selected = shallowRef(makeTarget({ readOnly: 'revision' }))
    const w = await render(['follow'])
    expect(w.get('[data-test="follow-scope"] [data-value="full"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="follow-scope-reason"]').text()).toContain('읽기 전용')
    await w.get('[data-test="follow-scope"] [data-value="full"]').trigger('click'); await flushPromises()
    expect(mocks.setting).not.toHaveBeenCalled()
  })
  it('the report section card opens the follow-up window once confirmation is complete', async () => {
    server = completeServer()
    // One app: the section card and the probe share the card session.
    wrapper = mount(defineComponent({ setup: () => () => h('div', [h(Probe), h(TestSpecTab, { target: selected.value })]) }),
      { global: { plugins: [i18n, createPinia()], stubs: { teleport: true } }, attachTo: document.body })
    await flushPromises()
    await wrapper.get('[data-test="test-card-follow"]').trigger('click'); await flushPromises()
    expect(context.windows.follow.open).toBe(true)
  })
  it('cannot open before confirmation is complete', async () => {
    await render([])
    expect(context.openReason('follow')).toBe('guard.no_result'); expect(context.openWindow('follow')).toBe(false)
  })
})

describe('0071 T3 report window — previous result, live run isolation, rerun end and run numbers', () => {
  it('a stale previous result shows the stored failure, actual, evidence and gate, not the effective NOT_RUN rows', async () => {
    server = staleServer()
    const w = await render()
    expect(w.get('[data-test="report-tag"]').text()).toBe('이전 결과 · 소스 변경')
    expect(w.findAll('[data-test="report-cell"]').map(cell => cell.attributes('data-state'))).toEqual(['fail', 'pass', 'not_run'])
    expect(w.get('[data-test="report-gate"]').text()).toContain('다음 단계 중단')
    expect(w.findAll('[data-test="report-gate-case"]').map(item => item.text())).toEqual(['TC-1'])
    expect(detailCase(w)).toBe('TC-1')
    expect(w.get('[data-test="report-actual"]').text()).toContain('Old failure'); expect(w.get('[data-test="report-evidence"]').text()).toContain('old.log')
    expect(w.find('[data-test="report-decision"]').exists()).toBe(false)
  })
  it('a rewritten scenario does not lend its title, requirement or code to the previous result; deleted cases stay listed', async () => {
    server = staleServer({ cases: [{ ...CASES[0], title: 'New expiry', requirement: 'R §9', automation_ref: 'tests/new.py::test_9' }] })
    const w = await render()
    expect(w.findAll('[data-test="report-row"]').map(row => row.attributes('data-case'))).toEqual(['TC-1', 'TC-2', 'TC-3'])
    const detail = w.get('[data-test="report-detail"]').text()
    expect(detail).toContain('Old expiry'); expect(detail).toContain('Old notice')
    expect(detail).not.toContain('New expiry'); expect(detail).not.toContain('R §9'); expect(detail).not.toContain('tests/new.py::test_9')
  })
  it('a new full run over an earlier FAIL / PASS with evidence shows running and queued cells with no earlier actual or evidence', async () => {
    server.effective_result = { summary: { overall: 'FAIL', counts: counts(1, 1, 0, 1) }, cases: [
      { case_id: 'TC-1', status: 'FAIL', actual: 'Old failure', evidence: [{ kind: 'log', value: 'old.log' }] },
      { case_id: 'TC-2', status: 'PASS', actual: 'Old pass', evidence: [{ kind: 'log', value: 'pass.log' }] }] }
    server.active_run = liveRun('run-all', { selected_case_ids: ['TC-1', 'TC-2'], reported_case_ids: [], created_at: '2026-10-11T10:00:00Z' })
    const w = await render()
    expect(w.get('[data-test="report-tag"]').text()).toBe('실행 중 0/2')
    for (const id of ['TC-1', 'TC-2']) {
      await w.get(`[data-test="report-row"][data-case="${id}"]`).trigger('click')
      expect(detailCase(w)).toBe(id)
      expect(w.get('[data-test="report-actual"]').text()).toContain('실행 중')
      expect(w.find('[data-test="report-evidence"]').exists()).toBe(false)
    }
    expect(w.text()).not.toContain('Old failure'); expect(w.text()).not.toContain('Old pass')
    expect(w.text()).not.toContain('old.log'); expect(w.text()).not.toContain('pass.log')
  })
  describe('a rerun of a confirmation target, judged when the run-finished event reloads the result', () => {
    let stream: ReturnType<typeof installProjectEventStream>
    beforeEach(() => {
      server = rerunServer()
      mocks.retry.mockImplementation(async (_doc: string, caseId: string) => {
        const run = liveRun('retry', { selected_case_ids: [caseId], reported_case_ids: [], created_at: '2026-10-11T10:00:00Z' })
        server.active_run = run; server.last_execution = run; server.run_history = [run, ...(server.run_history as unknown[])]
        return { runId: 'retry', status: 'running' }
      })
      vi.useFakeTimers(); stream = installProjectEventStream()
    })
    afterEach(() => { wrapper?.unmount(); wrapper = null; removeProjectEventStream(); vi.useRealTimers() })
    async function rerunTc2(filter: 'flagged' | 'all') {
      const w = await render(['report'], 'TC-2'); stream.open()
      if (filter === 'all') { await w.get('[data-test="report-filter"] [data-value="all"]').trigger('click'); await flushPromises() }
      expect(detailCase(w)).toBe('TC-2')
      await w.get('[data-test="report-other"]').trigger('click')
      await w.get('[data-test="report-cause"][data-cause="env"]').trigger('click'); await flushPromises()
      expect(mocks.retry).toHaveBeenCalledWith(TS_DOC, 'TC-2')
      // Accepted, not ended: the selection stays on the rerun case.
      expect(detailCase(w)).toBe('TC-2'); expect(w.get('[data-test="report-decision"]').text()).toContain('이 케이스 재실행 중')
      return w
    }
    async function finish(status: string, tc2: string) {
      const ended = runRow('retry', { status, overall: status === 'cancelled' ? null : tc2, selected_case_ids: ['TC-2'], reported_case_ids: status === 'cancelled' ? [] : ['TC-2'], created_at: '2026-10-11T10:00:00Z' })
      server.active_run = null; server.last_execution = ended; server.run_history = [ended, ...(server.run_history as unknown[]).slice(1)]
      if (status !== 'cancelled') server.effective_result = { summary: { overall: 'FAIL', counts: counts(tc2 === 'PASS' ? 1 : 0, tc2 === 'PASS' ? 1 : 2, 0, 1) },
        cases: [RERUN_ROWS[0], { ...RERUN_ROWS[1], status: tc2, actual: tc2 === 'PASS' ? 'ok' : 'Still failing' }] }
      stream.emit('test_run_finished', { docId: TS_DOC, groupId: base.flowId })
      await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS); await flushPromises()
    }
    for (const filter of ['flagged', 'all'] as const) {
      it(`a pass selects the next unchecked case after it (${filter} filter)`, async () => {
        const w = await rerunTc2(filter)
        await finish('passed', 'PASS')
        expect(context.presentation.value.stage).toBe(5)
        expect(detailCase(w)).toBe('TC-3')
      })
    }
    it('a failing rerun keeps the case selected as a confirmation target', async () => {
      const w = await rerunTc2('flagged')
      await finish('passed', 'FAIL')
      expect(detailCase(w)).toBe('TC-2'); expect(w.get('[data-test="report-actual"]').text()).toContain('Still failing')
    })
    it('a cancelled rerun keeps the case selected and says so', async () => {
      const w = await rerunTc2('all')
      await finish('cancelled', 'FAIL')
      expect(detailCase(w)).toBe('TC-2'); expect(w.get('[data-test="report-retry-outcome"]').text()).toContain('재실행 취소됨')
    })
  })
  for (const [name, ended] of [
    ['cancelled', { status: 'cancelled', overall: null }],
    ['prepare_failed', { status: 'failed', overall: null, prepare_refused: { error: 'runner_missing' } }],
    ['execution_failed', { status: 'failed', overall: null, error: 'runner_crashed' }],
  ] as const) {
    it(`after a valid result and a later ${name} run, the title, result head and highlighted run name the shown result`, async () => {
      const at = '2026-10-10T10:02:00Z'
      const result = runRow('tr_2', { overall: 'FAIL', summary: { counts: counts(0, 1, 1, 1) }, created_at: '2026-10-10T10:00:00Z', finished_at: at })
      const later = runRow('tr_3', { ...ended, created_at: '2026-10-11T10:00:00Z', finished_at: '2026-10-11T10:01:00Z' })
      server.latest_result = { ...result, cases: STORED }; server.last_execution = later; server.run_history = [later, result, runRow('tr_1')]
      const w = await render()
      expect(context.presentation.value.stage).toBe(3)
      expect(w.get('[data-test="report-tag"]').text()).toBe(`이전 결과 · 마지막 실행 ${{ cancelled: '취소됨', prepare_failed: '준비 실패', execution_failed: '실패' }[name]}`)
      const when = documentHistoryWhen(at)
      expect(w.get('[data-test="report-runs"]').text()).toBe(`↺ 실행 #2 · ${when} ▾`)
      expect(w.get('[data-test="report-summary"]').text().startsWith(`#2 · ${when} · `)).toBe(true)
      await w.get('[data-test="report-runs"]').trigger('click')
      const rows = w.findAll('[data-test="report-run-row"]')
      expect(rows.map(row => row.text().split(' · ')[0])).toEqual(['#3', '#2', '#1'])
      const current = rows.filter(row => row.classes('current'))
      expect(current).toHaveLength(1); expect(current[0].text().startsWith('#2 · ')).toBe(true); expect(current[0].text()).toContain(when)
    })
  }
})
