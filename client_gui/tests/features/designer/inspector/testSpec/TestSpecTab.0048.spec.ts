/**
 * flowgate.v02.0048 T#11 — the test section with `@/features/document` mocked as a small server that also
 * refuses (D#1 §12.1): the test view of a specification TS / its TSR / a legacy TS (run state, progress,
 * Case tally, result counts, the TSR's test gate, recent runs), ▶ 실행 (start, busy, read again), ■ 취소
 * (cancelling, then cancelled by the server's event — also when one server answer lists the run being cancelled
 * only in its history), the refusals — 403 (the permission answer keeps both
 * disabled with its reason for that document), 409 run_in_progress / doc_not_approved /
 * tsr_already_approved / failure_origin_pending, 422 no_automated_bindings, 423, 500, network — the guard
 * (AI running, disposed), the TS review state, the run events (test_run_started / test_case_finished /
 * test_run_finished of the TS, heard by its TSR too; other documents ignored), the read-only Flows, ↗ 열기
 * and a Node change (a late answer of the previous Node is dropped). Last, the real registry: the section
 * on TS / TSR cards in the documents tab above the documents list, none on V (same tab row), and a failed
 * test read leaves the documents tab as it is.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import TestSpecTab from '@/features/designer/inspector/features/testSpec/TestSpecTab.vue'
import { TEST_SPEC_EVENTS } from '@/features/designer/inspector/features/testSpec/useTestSpec'
import { createInspectorRegistry, inspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { prepareInspectorI18n, registrationModules, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'
import {
  LEGACY_TS_DOC,
  LEGACY_TS_NODE,
  TEST_FLOWS,
  TSR_DOC,
  TSR_NODE,
  TS_DOC,
  TS_NODE,
  V_NODE,
  counts,
  legacyDocument,
  liveRun,
  runRow,
  testSelection,
  tsDocument,
  tsrDocument,
} from './testSpecFixtures'

// The calls are mocked; normalization and error reading stay real.
const viewRead = vi.fn()
const startCall = vi.fn()
const approveCall = vi.fn()
const cancelCall = vi.fn()
const activityRead = vi.fn()
const documentRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const qaReadCall = vi.fn()
const attachmentsReadCall = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    testSpecRead: (...args: unknown[]) => viewRead(...args),
    documentHistoryRead: async (docId: string) => actual.toDocumentHistory({ doc_id: docId }),
    testSpecRunStart: (...args: unknown[]) => startCall(...args),
    reviewApprove: (...args: unknown[]) => approveCall(...args),
    testSpecRunCancel: (...args: unknown[]) => cancelCall(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    // The documents tab, the header actions, the Q&A and attachments tab numbers share the Frame.
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaReadCall(...args),
    attachmentsRead: (...args: unknown[]) => attachmentsReadCall(...args),
  }
})

const { toTestSpecView, toTestSpecRunAck, toReviewActivity, toReviewDocument, toDocumentDetail, toQaThread, toAttachmentsList } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const FLOW = TEST_FLOWS.v01.flowId
const OTHER_TS = 'flowgate.v02.0048.0035-TS'

// --- The server ------------------------------------------------------------------------------------

interface TsState {
  review: string
  tsrReview: string
  history: Record<string, unknown>[]
  active: Record<string, unknown> | null
  last: Record<string, unknown> | null
}

const server = new Map<string, TsState>()
let allowed = true
let running = false
let disposed = false
let nextRun = 1
/** The next call of that name is this refusal (once). */
const refusals = new Map<string, unknown>()
/** The next call of that name waits for the test to release it. */
const holds = new Map<string, Promise<void>>()

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const flat = (status: number, error: string, fields: Record<string, unknown> = {}) => axiosError(status, { error, ...fields })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })
const locked = () => axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm', run_live: true } })

function refusalOf(call: string): unknown {
  const refusal = refusals.get(call)
  refusals.delete(call)
  return refusal
}

function hold(call: string): () => void {
  let release!: () => void
  holds.set(call, new Promise<void>((resolve) => (release = resolve)))
  return release
}

async function waitHold(call: string) {
  const held = holds.get(call)
  holds.delete(call)
  if (held) await held
}

function seed() {
  server.set(TS_DOC, { review: 'approved', tsrReview: 'pending_review', history: [runRow('tr_0')], active: null, last: runRow('tr_0') })
  server.set(OTHER_TS, { review: 'approved', tsrReview: 'pending_review', history: [], active: null, last: null })
}

const ts = (docId = TS_DOC) => server.get(docId)!

function viewOf(docId: string): Record<string, unknown> {
  if (docId === LEGACY_TS_DOC) return legacyDocument()
  const tsr = docId === TSR_DOC
  const state = ts(tsr ? TS_DOC : docId)
  const runFields = {
    run_history: state.active ? [state.active, ...state.history] : state.history,
    active_run: state.active,
    last_execution: state.active ?? state.last,
  }
  if (tsr) return tsrDocument({ ...runFields, report: state.history[0] ?? null, ts_review_status: state.review, doc_review_status: state.tsrReview })
  const latest = state.history[0] ?? null
  return tsDocument({
    doc_id: docId,
    ...runFields,
    latest_result: latest,
    effective_result: latest ? { summary: { overall: latest.overall, counts: (latest.summary as Record<string, unknown> | null)?.counts ?? null } } : null,
    doc_review_status: state.review,
    tsr_review_status: state.tsrReview,
  })
}

/** The worker finishes the live run (passed / cancelled) — what test_run_finished announces. */
/** A result finishes as `passed` whatever its verdict (`overall`); `failed` is an execution error. */
function finishLive(docId: string, status: 'passed' | 'failed' | 'cancelled', overall: string | null = 'PASS') {
  const state = ts(docId)
  const live = state.active!
  const done = { ...live, status, phase: 'finished', overall, summary: overall ? { counts: counts(2, 0, 0, 1) } : null, created_at: '2026-10-09T12:00:00Z' }
  state.active = null
  state.last = done
  if (status !== 'cancelled') state.history = [done, ...state.history]
}

function serve() {
  viewRead.mockImplementation(async (docId: string) => {
    await waitHold(`read:${docId}`)
    const refusal = refusalOf('read')
    if (refusal) throw refusal
    return toTestSpecView(viewOf(docId))
  })
  startCall.mockImplementation(async (tsDocId: string) => {
    await waitHold('start')
    const state = ts(tsDocId)
    const refusal =
      refusalOf('start') ??
      (!allowed ? flat(403, 'permission_denied') : null) ??
      (running ? locked() : null) ??
      (disposed ? flat(409, 'group_disposed', { doc_id: tsDocId }) : null) ??
      (state.tsrReview === 'approved' ? flat(409, 'tsr_already_approved', { doc_id: tsDocId }) : null) ??
      (state.active ? flat(409, 'run_in_progress', { run_id: state.active.run_id }) : null)
    if (refusal) throw refusal
    const runId = `tr_${nextRun++}`
    state.active = liveRun(runId, { phase: 'queued', reported_case_ids: [], created_at: '2026-10-09T11:00:00Z' })
    return toTestSpecRunAck({ ok: true, run_id: runId, doc_id: tsDocId, status: 'running', case_total: 2, selected_case_ids: ['TC-1', 'TC-2'] })
  })
  // The TS approval of a specification queues the run of its automated Cases in the same transaction (0684 D#1 §3-1).
  approveCall.mockImplementation(async (docId: string) => {
    await waitHold('approve')
    const state = ts(docId)
    const refusal = refusalOf('approve') ?? (!['pending_review', 'revised'].includes(state.review) ? axiosError(400, { detail: 'Invalid review transition' }) : null)
    if (refusal) throw refusal
    state.review = 'approved'
    state.active = liveRun(`tr_${nextRun++}`, { phase: 'queued', reported_case_ids: [], created_at: '2026-10-09T11:00:00Z' })
    return { document: null, trCommit: null }
  })
  cancelCall.mockImplementation(async (runId: string) => {
    await waitHold('cancel')
    const refusal = refusalOf('cancel') ?? (!allowed ? flat(403, 'permission_denied') : null)
    if (refusal) throw refusal
    const state = [...server.values()].find((entry) => entry.active?.run_id === runId)
    if (!state) throw flat(404, 'run_not_found', { run_id: runId })
    state.active = { ...state.active!, status: 'cancelling' }
    return toTestSpecRunAck({ ok: true, run_id: runId, status: 'cancelling' })
  })
  activityRead.mockImplementation(async (groupId: string) =>
    toReviewActivity(running ? { active: true, run_id: 'run_ai', status: 'running', group_id: groupId } : { active: false, group_id: groupId }),
  )
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(docRow(docId)))
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(docRow(docId)))
  relationsRead.mockImplementation(async () => [])
  qaReadCall.mockImplementation(async (docId: string) => toQaThread({ ok: true, qa: { doc_id: docId, status: null, items: [] } }))
  attachmentsReadCall.mockImplementation(async (docId: string) => toAttachmentsList({ data: { doc_id: docId, attachments: [], count: 0 } }))
}

const docRow = (docId: string) => ({
  doc_id: docId,
  group_id: docId.split('.').slice(0, 3).join('.'),
  type_code: docId.split('-').pop(),
  title: docId,
  status: 'open',
  doc_review_status: 'approved',
  group_disposed: disposed,
})

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null
// flowgate.v02.0053 T#3: ↗ 열기 opens the document window in this tab — `opened()` lists the windows as
// [doc_id, mode]; a browser tab is never opened (`browserTabs` stays empty).
let browserTabs: unknown[][]
const opened = () => documentWindowList().map((win) => [win.docId, win.mode])

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  server.clear()
  refusals.clear()
  holds.clear()
  allowed = true
  running = false
  disposed = false
  nextRun = 1
  for (const mock of [viewRead, startCall, approveCall, cancelCall, activityRead, documentRead, detailRead, relationsRead, qaReadCall, attachmentsReadCall]) mock.mockReset()
  seed()
  serve()
  closeAllDocumentWindows()
  browserTabs = []
  vi.spyOn(window, 'open').mockImplementation((...args: unknown[]) => {
    browserTabs.push(args)
    return null
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  expect(browserTabs).toEqual([])
  closeAllDocumentWindows()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

async function mountTab(target: InspectorTarget) {
  wrapper = mount(TestSpecTab, { props: { target }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const target = (flow: keyof typeof TEST_FLOWS = 'v01', node = TS_NODE) => targetOf(testSelection(flow, node))
const otherTsTarget = (): InspectorTarget => ({ ...target(), docId: OTHER_TS, nodeId: `doc:${OTHER_TS}` })

type W = VueWrapper<any>
const el = (w: W, name: string) => w.find(`[data-test="${name}"]`)
const isDisabled = (w: W, name: string) => el(w, name).attributes('disabled') !== undefined
const stateOf = (w: W) => el(w, 'test-spec-state').attributes('data-state')
const textOf = (w: W, name: string) => el(w, name).text()
/** flowgate.v02.0094: why ▶ / ■ cannot be used is the visible button's tooltip (no reason line under the report). */
const reasonOf = (w: W) => {
  expect(el(w, 'test-spec-reason').exists()).toBe(false)
  return (el(w, 'test-spec-cancel').exists() ? el(w, 'test-spec-cancel') : el(w, 'test-spec-run')).attributes('title')
}
async function historyRuns(w: W) {
  await click(w, 'test-card-run-history')
  return [...document.querySelectorAll('[data-test="test-spec-history-row"]')].map((row) => row.getAttribute('data-run'))
}

async function click(w: W, name: string) {
  await el(w, name).trigger('click')
  await flushPromises()
}

async function emit(type: string, docId: string | null, groupId: string | null = FLOW) {
  stream.emit(type, { docId, groupId })
  await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
  await flushPromises()
}

// --- Reading ---------------------------------------------------------------------------------------

describe('test section — reading (flowgate.v02.0048 T#11)', () => {
  it('a specification TS: one read of its test view; state, verdict, Case tally, counts, recent runs, ▶ 실행 and the open buttons', async () => {
    const w = await mountTab(target())
    expect(viewRead).toHaveBeenCalledTimes(1)
    expect(viewRead).toHaveBeenCalledWith(TS_DOC)
    expect(el(w, 'inspector-test-spec').attributes('data-form')).toBe('spec')
    expect(stateOf(w)).toBe('done')
    expect(textOf(w, 'test-spec-state')).toBe('확인 필요 1')
    // flowgate.v02.0094: the 시나리오 line is its state badge only — no case tally beside it.
    expect(el(w, 'test-card-scenario-summary').exists()).toBe(false)
    expect(el(w, 'test-card-instruction-summary').exists()).toBe(false)
    expect(textOf(w, 'test-card-report-summary')).toBe('통과 2 · 실패 0 · 막힘 0 · 미실행 1')
    expect(await historyRuns(w)).toEqual(['tr_0'])
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
    expect(el(w, 'test-spec-cancel').exists()).toBe(false)
    expect(el(w, 'test-spec-reason').exists()).toBe(false)
    expect(el(w, 'test-spec-open').exists()).toBe(true)
    expect(isDisabled(w, 'test-spec-open')).toBe(false)
    expect(el(w, 'test-spec-open-spec').exists()).toBe(false)
    expect(el(w, 'test-spec-gate').exists()).toBe(false)
  })

  it('a TSR: its TS’s runs, the test gate, ↗ 테스트 지시; ▶ 실행 runs its TS', async () => {
    const w = await mountTab(target('v01', TSR_NODE))
    expect(viewRead).toHaveBeenCalledWith(TSR_DOC)
    expect(el(w, 'test-spec-gate').exists()).toBe(false)
    expect(el(w, 'test-card-scenario-open').exists()).toBe(true)
    expect(el(w, 'test-spec-open-report').exists()).toBe(false)
    // A TSR's view carries no Cases: no tally, and the server judges what runs.
    expect(el(w, 'test-spec-tally').exists()).toBe(false)
    await click(w, 'test-spec-run')
    expect(startCall).toHaveBeenCalledTimes(1)
    expect(startCall).toHaveBeenCalledWith(TS_DOC)
    expect(stateOf(w)).toBe('queued')
  })

  it('opens the same authoring surface for an existing TS without the retired legacy notice', async () => {
    const w = await mountTab(target('v01', LEGACY_TS_NODE))
    expect(el(w, 'inspector-test-spec').attributes('data-form')).toBe('legacy')
    expect(w.find('[data-test="test-spec-legacy"]').exists()).toBe(false)
    expect(w.findAll('[data-slot]').length).toBe(3)
  })

  it('T2 scenario and T3 report entries are connected; opening them is independent of execution', async () => {
    const w = await mountTab(target())
    expect(isDisabled(w, 'test-card-scenario-open')).toBe(false)
    expect(el(w, 'test-card-scenario-open').attributes('title')).not.toBe('연결 예정')
    await click(w, 'test-card-scenario-open')
    expect(isDisabled(w, 'test-spec-open')).toBe(false)
    expect(el(w, 'test-spec-open').attributes('title')).toBe('문서 열기')
    await click(w, 'test-spec-open')
    expect(opened()).toEqual([])
    expect(startCall).not.toHaveBeenCalled()
  })

  it.each([
    ['403', axiosError(403, { detail: 'Permission denied' }), '이 문서의 테스트 정보를 볼 권한이 없습니다.'],
    ['404', axiosError(404, { detail: 'Document not found: x' }), '문서를 찾을 수 없습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
    ['500', axiosError(500, { detail: 'boom' }), '테스트 실행 정보를 불러오지 못했습니다.'],
  ])('a failed read (%s) shows why with a retry that reads again', async (_label, refusal, text) => {
    refusals.set('read', refusal)
    const w = await mountTab(target())
    expect(el(w, 'inspector-pane-state').attributes('data-state')).toBe('error')
    expect(w.text()).toContain(text)
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    await click(w, 'inspector-pane-retry')
    expect(viewRead).toHaveBeenCalledTimes(2)
    expect(stateOf(w)).toBe('done')
  })
})

// --- Run and cancel --------------------------------------------------------------------------------

describe('test section — ▶ 실행 and ■ 취소 (flowgate.v02.0048 T#11)', () => {
  it('▶ 실행 starts once, waits busy, then reads the view and the guard again: queued, ■ 취소 instead of ▶ 실행', async () => {
    const w = await mountTab(target())
    const release = hold('start')
    await el(w, 'test-spec-run').trigger('click')
    await flushPromises()
    expect(startCall).toHaveBeenCalledTimes(1)
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    await el(w, 'test-spec-run').trigger('click')
    await flushPromises()
    expect(startCall).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    expect(viewRead).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(stateOf(w)).toBe('queued')
    expect(el(w, 'test-spec-run').exists()).toBe(false)
    expect(isDisabled(w, 'test-spec-cancel')).toBe(false)
    expect(await historyRuns(w)).toEqual(['tr_1', 'tr_0'])
    expect(el(w, 'test-spec-failure').exists()).toBe(false)
  })

  it('■ 취소 cancels the live run: cancelling (disabled with its reason) until the server’s finish event, then cancelled', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    await click(w, 'test-spec-run')
    await click(w, 'test-spec-cancel')
    expect(cancelCall).toHaveBeenCalledTimes(1)
    expect(cancelCall).toHaveBeenCalledWith('tr_1')
    expect(stateOf(w)).toBe('cancelling')
    expect(isDisabled(w, 'test-spec-cancel')).toBe(true)
    expect(reasonOf(w)).toBe('취소를 처리하는 중입니다.')
    await click(w, 'test-spec-cancel')
    expect(cancelCall).toHaveBeenCalledTimes(1)
    finishLive(TS_DOC, 'cancelled', null)
    await emit('test_run_finished', TS_DOC)
    expect(stateOf(w)).toBe('cancelled')
    expect(textOf(w, 'test-spec-state')).toBe('취소됨')
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
    expect(el(w, 'test-spec-cancel').exists()).toBe(false)
  })

  it('one server answer lists the run being cancelled only in its history: still cancelling (not the old result) until the finish event', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    await click(w, 'test-spec-run')
    // The server reads its run list and its live run separately (seen live: active_run null, last_execution cancelling).
    viewRead.mockImplementationOnce(async (docId: string) => toTestSpecView({ ...viewOf(docId), active_run: null }))
    await click(w, 'test-spec-cancel')
    expect(cancelCall).toHaveBeenCalledWith('tr_1')
    expect(stateOf(w)).toBe('cancelling')
    expect(el(w, 'test-spec-run').exists()).toBe(false)
    expect(isDisabled(w, 'test-spec-cancel')).toBe(true)
    expect(reasonOf(w)).toBe('취소를 처리하는 중입니다.')
    finishLive(TS_DOC, 'cancelled', null)
    await emit('test_run_finished', TS_DOC)
    expect(stateOf(w)).toBe('cancelled')
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
  })

  it.each([
    ['409 run_in_progress (started elsewhere)', 'run_in_progress', '이미 실행 중이거나 AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.'],
    ['409 doc_not_approved (unapproved meanwhile)', 'doc_not_approved', '테스트 지시가 승인되지 않아 거절되었습니다. 상태를 다시 읽었습니다.'],
    ['409 tsr_already_approved', 'tsr_already_approved', '결과 보고서가 이미 승인되어 거절되었습니다.'],
    ['409 failure_origin_pending', 'failure_origin_pending', 'FAIL 원인 검토가 남아 있어 거절되었습니다. 문서 창에서 처리합니다.'],
    ['422 no_automated_bindings', 'no_automated_bindings', '서버가 실행할 수 있는 자동 케이스가 없어 거절되었습니다.'],
    ['423 (an AI run took the group)', 'locked', '이미 실행 중이거나 AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.'],
    ['409 another code', 'odd_state', '서버 상태가 바뀌어 실행이 거절되었습니다. 상태를 다시 읽었습니다.'],
    ['500', 'server', '테스트를 실행하지 못했습니다.'],
    ['network', 'network', '서버에 연결하지 못했습니다.'],
  ])('a refused start (%s) shows the refusal and reads the view and the guard again', async (_label, code, text) => {
    const w = await mountTab(target())
    if (code === 'run_in_progress') ts().active = liveRun('tr_other')
    if (code === 'doc_not_approved') ts().review = 'revised'
    if (code === 'tsr_already_approved') ts().tsrReview = 'approved'
    if (code === 'locked') running = true
    refusals.set(
      'start',
      code === 'locked'
        ? locked()
        : code === 'server'
          ? axiosError(500, { error: 'internal_error' })
          : code === 'network'
            ? networkError()
            : flat(code === 'no_automated_bindings' ? 422 : 409, code),
    )
    await click(w, 'test-spec-run')
    expect(startCall).toHaveBeenCalledTimes(1)
    expect(textOf(w, 'test-spec-failure')).toBe(text)
    expect(viewRead).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    // What the read again found decides the buttons now.
    if (code === 'run_in_progress') {
      expect(stateOf(w)).toBe('executing')
      expect(isDisabled(w, 'test-spec-cancel')).toBe(false)
    } else if (code === 'doc_not_approved') {
      // revised with an earlier run may run again: the server decides; the view does not block it.
      expect(isDisabled(w, 'test-spec-run')).toBe(false)
    } else if (code === 'tsr_already_approved') {
      expect(isDisabled(w, 'test-spec-run')).toBe(true)
      expect(reasonOf(w)).toBe('결과 보고서가 이미 승인되어 다시 실행할 수 없습니다.')
    } else if (code === 'locked') {
      expect(reasonOf(w)).toBe('AI 실행 중이라 테스트를 실행할 수 없습니다.')
    } else {
      expect(isDisabled(w, 'test-spec-run')).toBe(false)
    }
  })

  it('403 on start: the refusal, then ▶ 실행 disabled with the permission reason; nothing more is sent for this document', async () => {
    allowed = false
    const w = await mountTab(target())
    // No permission list on the client: the operation is offered until the server answers.
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
    await click(w, 'test-spec-run')
    expect(textOf(w, 'test-spec-failure')).toBe('권한이 없어 거절되었습니다. 관리자 또는 테스트 실행 권한이 필요합니다.')
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    expect(reasonOf(w)).toBe('테스트 실행 권한(관리자 또는 테스트 실행 권한)이 없어 서버가 거절했습니다.')
    await click(w, 'test-spec-run')
    expect(startCall).toHaveBeenCalledTimes(1)
    // Reading and opening are queries: still possible.
    await click(w, 'test-spec-open')
    expect(opened()).toHaveLength(0)
    expect(isDisabled(w, 'test-spec-open')).toBe(false)
  })

  it('403 holds for the document it was answered on: another TS offers ▶ 실행 again (and is refused again by the server)', async () => {
    allowed = false
    const w = await mountTab(target())
    await click(w, 'test-spec-run')
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    await w.setProps({ target: otherTsTarget() })
    await flushPromises()
    expect(viewRead).toHaveBeenLastCalledWith(OTHER_TS)
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
    expect(el(w, 'test-spec-failure').exists()).toBe(false)
    await w.setProps({ target: target() })
    await flushPromises()
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
  })

  it('403 on cancel: ■ 취소 disabled with the permission reason; the live run goes on', async () => {
    const w = await mountTab(target())
    await click(w, 'test-spec-run')
    allowed = false
    await click(w, 'test-spec-cancel')
    expect(textOf(w, 'test-spec-failure')).toBe('권한이 없어 거절되었습니다. 관리자 또는 테스트 실행 권한이 필요합니다.')
    expect(isDisabled(w, 'test-spec-cancel')).toBe(true)
    expect(reasonOf(w)).toBe('테스트 실행 권한(관리자 또는 테스트 실행 권한)이 없어 서버가 거절했습니다.')
    expect(stateOf(w)).toBe('queued')
    await click(w, 'test-spec-cancel')
    expect(cancelCall).toHaveBeenCalledTimes(1)
  })

  it('a cancel of a run already gone (404): the refusal, and the read again shows the finished run', async () => {
    const w = await mountTab(target())
    await click(w, 'test-spec-run')
    finishLive(TS_DOC, 'passed')
    refusals.set('cancel', flat(404, 'run_not_found', { run_id: 'tr_1' }))
    await click(w, 'test-spec-cancel')
    expect(textOf(w, 'test-spec-failure')).toBe('문서나 실행을 찾을 수 없습니다. 상태를 다시 읽었습니다.')
    expect(stateOf(w)).toBe('done')
    expect(el(w, 'test-spec-cancel').exists()).toBe(false)
  })
})

// --- Guard and TS state ----------------------------------------------------------------------------

describe('test section — the guard and the TS state (flowgate.v02.0048 T#11)', () => {
  it('AI running: ▶ 실행 disabled with the reason, nothing sent; ai_invoke_finished of the Flow opens it again', async () => {
    vi.useFakeTimers()
    running = true
    const w = await mountTab(target())
    stream.open()
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    expect(reasonOf(w)).toBe('AI 실행 중이라 테스트를 실행할 수 없습니다.')
    await click(w, 'test-spec-run')
    expect(startCall).not.toHaveBeenCalled()
    running = false
    await emit('ai_invoke_finished', null)
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
  })

  it('a disposed group: disabled with the disposed reason', async () => {
    disposed = true
    const w = await mountTab(target())
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    expect(reasonOf(w)).toBe('폐기된 그룹이라 테스트를 실행할 수 없습니다.')
  })

  // flowgate.v02.0094 #8: written (지시서 · 시나리오 작성 완료) but awaiting its review and never run — nothing on the v0.2
  // card approves a TS on its own, so ▶ is open and approves it; the approval queues the run.
  it.each(['draft', 'pending_review', 'revised', 'rejected'])('a written TS (%s, never run) starts without a document approval', async (review) => {
    ts().review = review
    ts().history = []
    ts().last = null
    const w = await mountTab(target())
    expect(textOf(w, 'test-card-scenario-state')).toBe('작성 완료')
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
    expect(reasonOf(w)).toBe('테스트 코드 실행')
    await click(w, 'test-spec-run')
    expect(approveCall).not.toHaveBeenCalled()
    expect(startCall).toHaveBeenCalledWith(TS_DOC)
    expect(ts().review).toBe(review)
    expect(stateOf(w)).toBe('queued')
    expect(el(w, 'test-spec-failure').exists()).toBe(false)
  })
  it('a refused run on ▶ is told under the report and nothing starts', async () => {
    ts().review = 'pending_review'
    ts().history = []
    ts().last = null
    refusals.set('start', axiosError(403, { detail: 'Insufficient permissions' }))
    const w = await mountTab(target())
    await click(w, 'test-spec-run')
    expect(approveCall).not.toHaveBeenCalled()
    expect(startCall).toHaveBeenCalledOnce()
    expect(el(w, 'test-spec-failure').exists()).toBe(true)
  })
  it('an approved TS or one that already ran starts its run as before, without an approval', async () => {
    const w = await mountTab(target())
    await click(w, 'test-spec-run')
    expect(startCall).toHaveBeenCalledWith(TS_DOC)
    expect(approveCall).not.toHaveBeenCalled()
  })

  it('a draft TS remains runnable before and after a review event', async () => {
    vi.useFakeTimers()
    ts().review = 'draft'
    ts().history = []
    const w = await mountTab(target())
    stream.open()
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
    expect(reasonOf(w)).toBe('테스트 코드 실행')
    expect(el(w, 'test-card-run-history').exists()).toBe(false)
    ts().review = 'approved'
    await emit('doc_review_status_changed', TS_DOC)
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
  })
})

// --- Run events ------------------------------------------------------------------------------------

describe('test section — run events (flowgate.v02.0048 T#11)', () => {
  it('listens to the test run events of the TS', () => {
    expect(TEST_SPEC_EVENTS).toEqual(expect.arrayContaining(['test_run_started', 'test_run_phase', 'test_case_finished', 'test_run_finished']))
  })

  it('a run started by someone else: started -> executing with progress -> Case finished -> finished, each read again once', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    ts().active = liveRun('tr_9', { reported_case_ids: [] })
    await emit('test_run_started', TS_DOC)
    expect(viewRead).toHaveBeenCalledTimes(2)
    expect(stateOf(w)).toBe('executing')
    expect(textOf(w, 'test-spec-state')).toBe('실행 중 0/2')
    ts().active = liveRun('tr_9', { reported_case_ids: ['TC-1'] })
    stream.emit('test_case_finished', { docId: TS_DOC, groupId: FLOW })
    stream.emit('test_run_phase', { docId: TS_DOC, groupId: FLOW })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    // Two frames in the window: one read again.
    expect(viewRead).toHaveBeenCalledTimes(3)
    expect(textOf(w, 'test-spec-state')).toBe('실행 중 1/2')
    finishLive(TS_DOC, 'passed', 'FAIL')
    await emit('test_run_finished', TS_DOC)
    expect(stateOf(w)).toBe('done')
    expect(textOf(w, 'test-card-report-summary')).toContain('통과')
    expect(await historyRuns(w)).toEqual(['tr_9', 'tr_0'])
    expect(el(w, 'test-spec-run').exists()).toBe(true)
  })

  it('a run that fails to execute (status failed) shows the execution failure', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    await click(w, 'test-spec-run')
    finishLive(TS_DOC, 'failed', null)
    await emit('test_run_finished', TS_DOC)
    expect(stateOf(w)).toBe('execution_failed')
    expect(textOf(w, 'test-spec-state')).toBe('실행 실패')
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
  })

  it('a TSR hears its TS’s run frames', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target('v01', TSR_NODE))
    stream.open()
    ts().active = liveRun('tr_9')
    await emit('test_run_started', TS_DOC)
    expect(viewRead.mock.calls.filter(([id]) => id === TSR_DOC)).toHaveLength(2)
    expect(stateOf(w)).toBe('executing')
  })

  it('frames of another document or another Flow are ignored', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    await emit('test_run_started', OTHER_TS)
    await emit('test_run_finished', 'flowgate.v02.0049.0007-TS', 'flowgate.v02.0049')
    await emit('ai_invoke_started', TS_DOC)
    expect(viewRead).toHaveBeenCalledTimes(1)
  })

  it('a reconnect reads again', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    stream.reconnect()
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(viewRead).toHaveBeenCalledTimes(2)
  })
})

// --- Read-only Flows -------------------------------------------------------------------------------

describe('test section — read-only Flows (flowgate.v02.0048 T#11)', () => {
  it('Graph revision view: ▶ 실행 shown but disabled with the read-only reason; reading and ↗ 열기 work', async () => {
    const w = await mountTab(target('graphRevision'))
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    expect(reasonOf(w)).toBe('확정된 Graph revision 보기라 읽기 전용입니다. draft 보기에서 테스트를 실행합니다.')
    await click(w, 'test-spec-run')
    expect(startCall).not.toHaveBeenCalled()
    await click(w, 'test-spec-open')
    expect(opened()).toHaveLength(0)
    expect(isDisabled(w, 'test-spec-open')).toBe(false)
  })

  it('Graph draft view: enabled; ■ 취소 of a live run in revision view is disabled the same way', async () => {
    expect(isDisabled(await mountTab(target('graph')), 'test-spec-run')).toBe(false)
    wrapper!.unmount()
    ts().active = liveRun('tr_9')
    const w = await mountTab(target('graphRevision'))
    expect(isDisabled(w, 'test-spec-cancel')).toBe(true)
    expect(reasonOf(w)).toBe('확정된 Graph revision 보기라 읽기 전용입니다. draft 보기에서 테스트를 실행합니다.')
  })

  it('ownership conflict: the conflict reason', async () => {
    const w = await mountTab(target('conflict'))
    expect(isDisabled(w, 'test-spec-run')).toBe(true)
    expect(reasonOf(w)).toBe('Flow 소유가 충돌해 읽기 전용입니다.')
  })

  it('unassigned Flow: enabled', async () => {
    expect(isDisabled(await mountTab(target('unassigned')), 'test-spec-run')).toBe(false)
  })
})

// --- Node change -----------------------------------------------------------------------------------

describe('test section — Node change (flowgate.v02.0048 T#11)', () => {
  it('a late answer of the previous Node does not cover the new Node', async () => {
    const release = hold(`read:${TS_DOC}`)
    const w = mount(TestSpecTab, { props: { target: target() }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    wrapper = w
    await flushPromises()
    await w.setProps({ target: target('v01', LEGACY_TS_NODE) })
    await flushPromises()
    expect(el(w, 'inspector-test-spec').attributes('data-form')).toBe('legacy')
    release()
    await flushPromises()
    expect(el(w, 'inspector-test-spec').attributes('data-form')).toBe('legacy')
    expect(el(w, 'test-spec-run').exists()).toBe(false)
  })

  it('a refusal belongs to its document: it is gone on another Node', async () => {
    refusals.set('start', flat(409, 'failure_origin_pending'))
    const w = await mountTab(target())
    await click(w, 'test-spec-run')
    expect(el(w, 'test-spec-failure').exists()).toBe(true)
    await w.setProps({ target: otherTsTarget() })
    await flushPromises()
    expect(el(w, 'test-spec-failure').exists()).toBe(false)
  })

  it('a start answered after the Node changed leaves no refusal on the new Node and no read of the old one', async () => {
    const w = await mountTab(target())
    const release = hold('start')
    refusals.set('start', flat(409, 'run_in_progress'))
    await el(w, 'test-spec-run').trigger('click')
    await flushPromises()
    await w.setProps({ target: otherTsTarget() })
    await flushPromises()
    const reads = viewRead.mock.calls.length
    release()
    await flushPromises()
    expect(el(w, 'test-spec-failure').exists()).toBe(false)
    expect(viewRead.mock.calls.length).toBe(reads)
    expect(isDisabled(w, 'test-spec-run')).toBe(false)
  })
})

// --- In the Frame (real registry) ------------------------------------------------------------------

describe('test section in the Frame (real registry)', () => {
  async function mountFrame(selection: FlowGraphSelection, registry = inspectorRegistry) {
    wrapper = mount(DesignerInspector, { props: { selection, registry }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  const tabLabels = (w: W) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((tab) => tab.text())

  it.each(['v01', 'graph', 'unassigned'] as const)('TS in a %s Flow: the documents tab shows the test section alone', async (flow) => {
    const w = await mountFrame(testSelection(flow, TS_NODE))
    const section = el(w, 'inspector-test-spec')
    expect(section.exists()).toBe(true)
    // flowgate.v02.0052: the common documents list is not repeated beneath the card.
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
    expect(viewRead).toHaveBeenCalledWith(TS_DOC)
  })

  it('TSR: the section too', async () => {
    const w = await mountFrame(testSelection('graph', TSR_NODE))
    expect(el(w, 'inspector-test-spec').exists()).toBe(true)
    expect(viewRead).toHaveBeenCalledWith(TSR_DOC)
  })

  it('V: no test section, no empty place, no read; the tab row is the same as without the registration', async () => {
    const w = await mountFrame(testSelection('v01', V_NODE))
    expect(el(w, 'inspector-test-spec').exists()).toBe(false)
    expect(viewRead).not.toHaveBeenCalled()
    const withSection = tabLabels(w)
    w.unmount()
    const modules = registrationModules(inspectorRegistry.entries.filter((entry) => entry.key !== 'testSpec'))
    const w2 = await mountFrame(testSelection('v01', V_NODE), createInspectorRegistry(modules))
    expect(tabLabels(w2)).toEqual(withSection)
    expect(w2.find('[data-test="inspector-documents"]').exists()).toBe(true)
  })

  it('a failed test read stays in the test section', async () => {
    viewRead.mockRejectedValue(axiosError(500, { detail: 'boom' }))
    const w = await mountFrame(testSelection('v01', TS_NODE))
    expect(el(w, 'inspector-test-spec').find('[data-test="inspector-pane-state"]').attributes('data-state')).toBe('error')
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
  })

  it('▶ 실행 in the Frame starts the run of the selected TS', async () => {
    const w = await mountFrame(testSelection('unassigned', TS_NODE))
    await click(w, 'test-spec-run')
    expect(startCall).toHaveBeenCalledWith(TS_DOC)
    expect(stateOf(w)).toBe('queued')
  })
})

// flowgate.v02.0094 #1-#3 — the test card lines follow the other cards' slot lines: a state badge without extra words,
// icon buttons whose names are their tooltips, and no reason line under the report.
describe('test section — the existing card rules (flowgate.v02.0094)', () => {
  it('every line button is an icon button with a tooltip; no text button, summary or reason line', async () => {
    const w = await mountTab(target())
    const buttons = w.findAll('[data-test="inspector-slot-buttons"] button')
    expect(buttons.length).toBeGreaterThan(0)
    for (const button of buttons) {
      expect(button.classes(), button.attributes('data-test')).toContain('ui-icon-btn')
      expect(button.attributes('title'), button.attributes('data-test')).toBeTruthy()
      expect(button.attributes('aria-label'), button.attributes('data-test')).toBeTruthy()
    }
    expect(el(w, 'test-card-brief').text()).toBe('✎')
    expect(el(w, 'test-card-brief').attributes('aria-label')).toBe('편집')
    expect(el(w, 'test-spec-run').text()).toBe('▶')
    expect(el(w, 'test-spec-run').attributes('title')).toBe('테스트 코드 실행')
    expect(el(w, 'test-card-run-history').attributes('title')).toBe('실행 #1')
    expect(el(w, 'test-card-instruction-summary').exists()).toBe(false)
    expect(el(w, 'test-card-scenario-summary').exists()).toBe(false)
    expect(textOf(w, 'test-card-scenario-state')).toBe('작성 완료')
    expect(el(w, 'test-spec-reason').exists()).toBe(false)
  })
  it("the 시나리오 line's AI writing is the cards' [AI 호출] robot button (the 지시서 window only saves)", async () => {
    const w = await mountTab(target())
    const ai = el(w, 'test-card-scenario-ai')
    expect(ai.attributes('data-icon')).toBe('robot')
    expect(ai.classes()).toEqual(expect.arrayContaining(['ui-icon-btn', 'inspector-slot-ai']))
    expect(ai.attributes('aria-label')).toBe('AI 호출')
    expect(ai.attributes('title')).toBe('AI 호출')
    expect(ai.attributes('disabled')).toBeUndefined()
    expect(w.find('[data-slot="instruction"] [data-test="test-card-scenario-ai"]').exists()).toBe(false)
  })
})
