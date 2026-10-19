/**
 * flowgate.v02.0048 T#13 — the WP summary with `@/features/document` mocked as a small server that also refuses
 * (D#1 §12.1), as flowgate.v02.0056 F1 (0056 CH §5) left it: the two slot lines (작업계획 · 전개) without the
 * `rev · Step` summary, and under them only what an operator acts on — the progress (cards the workflow
 * started) and the warning when Steps have no AI; the 0048 details (rev, totals, provider assignment, one row
 * per card, "Step 편집은 …", the details' open button) are gone. Still here: the refusals — 409 wp_unreadable
 * (its reason), 403, 404, 422, 500, network — and the retry, the plan's change events (a save / restore /
 * fill of the plan: document_explorer_refresh / group_view_refresh; a review: doc_review_status_changed; a
 * document of the same Flow added or updated: a card started; frames of other Flows and other review changes
 * ignored; a reconnect), the read-only Flows and a Node change (a late answer of the previous Node is
 * dropped). Last, the real registry: the summary on WP cards in the documents tab in every ownership, none
 * on other cards or a WP Node without a document (same tab row), a failed read leaves the documents tab as it
 * is, and no undecided wording.
 * flowgate.v02.0072 T#3: the documents area is the one 「작업계획」 line (no 전개 line), and the line opens the plan in the
 * v0.2 work plan edit window (features/workPlanWindow) — not the document window; `wpOpened()` lists those windows.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import WorkPlanTab from '@/features/designer/inspector/features/workPlan/WorkPlanTab.vue'
import { WORK_PLAN_EVENTS } from '@/features/designer/inspector/features/workPlan/useWorkPlan'
import { createInspectorRegistry, inspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { closeAllDocumentWindows, documentHistoryWindowList, documentWindowList } from '@/features/documentWindow'
import { closeAllWorkPlanEditWindows, workPlanEditWindowList } from '@/features/workPlanWindow'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { V01_TASK_NODE, prepareInspectorI18n, registrationModules, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'
import {
  OTHER_WP_DOC,
  OTHER_WP_NODE,
  PLAN_FLOWS,
  WP_DOC,
  WP_EMPTY_NODE,
  WP_NODE,
  pairRows,
  planSelection,
  planSteps,
  stepRow,
  workPlanDocument,
} from './workPlanFixtures'

// The calls are mocked; normalization and error reading stay real.
const planRead = vi.fn()
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
    workPlanRead: (...args: unknown[]) => planRead(...args),
    // The documents tab, the header actions, the Q&A and attachments tab numbers share the Frame.
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaReadCall(...args),
    attachmentsRead: (...args: unknown[]) => attachmentsReadCall(...args),
  }
})

const { toWorkPlanView, toReviewActivity, toReviewDocument, toDocumentDetail, toQaThread, toAttachmentsList } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const FLOW = PLAN_FLOWS.v01.flowId
const NEW_TASK_DOC = 'flowgate.v02.0048.0040-T'

// --- The server ------------------------------------------------------------------------------------

interface PlanState {
  revision: number
  steps: Record<string, unknown>[]
  started: string[]
  editable: boolean
  lockedReason: string | null
  /** The stored plan cannot be read as a table (409 wp_unreadable). */
  unreadable: boolean
}

const server = new Map<string, PlanState>()
/** The next read is this refusal (once). */
const refusals: unknown[] = []
/** The next read of that document waits for the test to release it. */
const holds = new Map<string, Promise<void>>()

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })

function hold(docId: string): () => void {
  let release!: () => void
  holds.set(docId, new Promise<void>((resolve) => (release = resolve)))
  return release
}

function seed() {
  server.set(WP_DOC, { revision: 3, steps: planSteps(), started: ['D#1'], editable: true, lockedReason: null, unreadable: false })
  server.set(OTHER_WP_DOC, { revision: 0, steps: [stepRow('D', 1)], started: [], editable: true, lockedReason: null, unreadable: false })
}

const wp = (docId = WP_DOC) => server.get(docId)!

function serve() {
  planRead.mockImplementation(async (docId: string) => {
    const held = holds.get(docId)
    holds.delete(docId)
    if (held) await held
    const refusal = refusals.shift()
    if (refusal) throw refusal
    const state = server.get(docId)
    if (!state) throw axiosError(404, { detail: `Document not found: ${docId}` })
    if (state.unreadable) throw axiosError(409, { code: 'wp_unreadable', message: 'm', reason: 'not_json', detail: null, revision_no: state.revision, revisions: [] })
    return toWorkPlanView(
      workPlanDocument(
        {
          doc_id: docId,
          revision_no: state.revision,
          steps: state.steps,
          editable: state.editable,
          edit_locked_reason: state.lockedReason,
          doc_review_status: state.editable ? 'pending_review' : 'approved',
        },
        state.started,
      ),
    )
  })
  activityRead.mockImplementation(async (groupId: string) => toReviewActivity({ active: false, group_id: groupId }))
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
  group_disposed: false,
})

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null
// flowgate.v02.0053 T#3: the lines open the document window in this tab — `opened()` lists the windows as
// [doc_id, mode]; a browser tab is never opened (`browserTabs` stays empty).
let browserTabs: unknown[][]
const opened = () => documentWindowList().map((win) => [win.docId, win.mode])
const historyOpened = () => documentHistoryWindowList().map((win) => win.docId)
/** flowgate.v02.0072 T#3: the work plan edit windows (the WP line opens its plan there). */
const wpOpened = () => workPlanEditWindowList().map((win) => win.docId)

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  server.clear()
  refusals.length = 0
  holds.clear()
  for (const mock of [planRead, activityRead, documentRead, detailRead, relationsRead, qaReadCall, attachmentsReadCall]) mock.mockReset()
  seed()
  serve()
  closeAllDocumentWindows()
  closeAllWorkPlanEditWindows()
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
  closeAllWorkPlanEditWindows()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

async function mountTab(target: InspectorTarget) {
  wrapper = mount(WorkPlanTab, { props: { target }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const target = (flow: keyof typeof PLAN_FLOWS = 'v01', node = WP_NODE) => targetOf(planSelection(flow, node))

type W = VueWrapper<any>
const el = (w: W, name: string) => w.find(`[data-test="${name}"]`)
const textOf = (w: W, name: string) => el(w, name).text()
const paneState = (w: W) => el(w, 'inspector-work-plan').find('[data-test="inspector-pane-state"]')
const planLine = (w: W) => w.get('[data-test="inspector-slot"][data-slot="plan"]')

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

describe('WP summary — reading (flowgate.v02.0048 T#13, 0056 F1)', () => {
  it('one read of the plan: the lines, the progress and the unassigned warning — nothing else', async () => {
    const w = await mountTab(target())
    expect(planRead).toHaveBeenCalledTimes(1)
    expect(planRead).toHaveBeenCalledWith(WP_DOC)
    expect(el(w, 'inspector-work-plan').attributes('data-status')).toBe('ready')
    expect(w.findAll('[data-test="inspector-slot-name"]').map((name) => name.text())).toEqual(['작업계획'])
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 1/4')
    expect(el(w, 'work-plan-progress').attributes('data-started')).toBe('1')
    expect(el(w, 'work-plan-progress').attributes('data-cards')).toBe('4')
    expect(textOf(w, 'work-plan-unassigned')).toBe('⚠담당 AI 미지정 Step 1개')
    expect(el(w, 'work-plan-unassigned').attributes('role')).toBe('status')
    expect(paneState(w).exists()).toBe(false)
    // 0056 F1: the details are gone — no rev, no totals, no assignment list, no per-card rows, no edit line,
    // no second open button; the plan line carries no `rev · Step` summary.
    for (const name of ['work-plan-details', 'work-plan-rev', 'work-plan-totals', 'work-plan-assignments', 'work-plan-card', 'work-plan-edit', 'work-plan-open', 'work-plan-empty']) {
      expect(el(w, name).exists(), name).toBe(false)
    }
    expect(planLine(w).find('[data-test="inspector-slot-summary"]').exists()).toBe(false)
    const text = el(w, 'inspector-work-plan').text()
    for (const word of ['rev', 'Step 요약', '공급자', 'Claude', 'Codex', 'D#1', 'T#1', '편집기', '상세 정보']) expect(text).not.toContain(word)
  })

  it('the plan line opens the plan in its work plan edit window in this tab (not the document window); nothing else is called', async () => {
    const w = await mountTab(target())
    await planLine(w).get('[data-test="inspector-slot-open"]').trigger('click')
    await flushPromises()
    expect(wpOpened()).toEqual([WP_DOC])
    expect(opened()).toEqual([])
    expect(planRead).toHaveBeenCalledTimes(1)
  })

  it('the plan line has the history button, which opens the plan’s 검수·반려 이력 window (0056 F1)', async () => {
    const w = await mountTab(target())
    const history = planLine(w).get('[data-test="inspector-slot-history"]')
    expect(history.attributes('title')).toBe('검수·반려 이력')
    await history.trigger('click')
    await flushPromises()
    expect(historyOpened()).toEqual([WP_DOC])
    expect(opened()).toEqual([])
  })

  it('a finally confirmed or locked plan reads the same (no edit line any more)', async () => {
    Object.assign(wp(), { editable: false, lockedReason: 'final_approved' })
    const w = await mountTab(target())
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 1/4')
    expect(el(w, 'work-plan-edit').exists()).toBe(false)
    expect(el(w, 'inspector-work-plan').text()).not.toContain('편집')
  })

  it('a fully assigned plan: no warning', async () => {
    wp().steps = [stepRow('AC', 1, { locked: true, locked_reason: 'server_assembled', origin: 'system' })]
    planRead.mockImplementationOnce(async () =>
      toWorkPlanView(workPlanDocument({ steps: wp().steps, unassigned_step_count: 0, assignment_summary: [], totals: { design_sheets: 0, work_sets: 0, steps: 1 } }, [])),
    )
    const w = await mountTab(target())
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 0/1')
    expect(el(w, 'work-plan-unassigned').exists()).toBe(false)
  })

  // flowgate.v02.0074 T#1 (R §1.1): no cards is a wordless dashed block, not a sentence.
  it('a plan without Steps: no cards — the wordless dashed block', async () => {
    wp().steps = []
    const w = await mountTab(target())
    expect(textOf(w, 'work-plan-progress')).toBe('')
    expect(el(w, 'work-plan-progress').classes()).toContain('is-empty')
    expect(el(w, 'work-plan-progress').attributes('aria-hidden')).toBe('true')
    expect(el(w, 'work-plan-progress').attributes('data-cards')).toBe('0')
  })

  it('the English and Japanese wording', async () => {
    i18n.global.locale.value = 'en'
    let w = await mountTab(target())
    expect(textOf(w, 'work-plan-progress')).toBe('Cards started 1/4')
    expect(textOf(w, 'work-plan-unassigned')).toBe('⚠1 Step(s) without an AI')
    w.unmount()
    i18n.global.locale.value = 'ja'
    w = await mountTab(target())
    expect(textOf(w, 'work-plan-progress')).toBe('開始したカード 1/4')
  })
})

// --- flowgate.v02.0074 T#1 — the empty states ---------------------------------------------------------

describe('flowgate.v02.0074 T#1', () => {
  const OLD_NO_CARDS = ['계획에 카드가 없습니다', 'The plan has no cards', '計画にカードがありません']

  /** A placed WP card whose plan line has no document yet (the line offers [작성]). */
  function emptyPlanCard(): InspectorTarget {
    const node: FlowGraphNode = {
      ...WP_EMPTY_NODE,
      id: 'gn_wp_empty',
      definition: { nodeType: 'document', docType: 'WP', paletteItemId: null, label: '작업계획', assignee: null, rootBound: false },
      cardDocuments: [{ slot: 'plan', docType: 'WP', author: 'human', docId: null, title: '작업계획', status: null, docReviewStatus: null }],
    }
    return targetOf(selectionOf({ ...PLAN_FLOWS.v01, nodes: [...PLAN_FLOWS.v01.nodes, node] }, node.id))
  }

  it('(a) no document: the plan line is empty with [작성]; no status, no progress', async () => {
    const w = await mountTab(emptyPlanCard())
    expect(planLine(w).classes()).toContain('is-empty')
    expect(planLine(w).attributes('data-empty')).toBe('true')
    expect(planLine(w).find('[data-test="inspector-slot-action"][data-action="write"]').exists()).toBe(true)
    expect(el(w, 'work-plan-status').exists()).toBe(false)
    expect(el(w, 'work-plan-progress').exists()).toBe(false)
    expect(planRead).not.toHaveBeenCalled()
  })

  it('(b) no cards: a normal plan line, the wordless dashed block, no text and no pane state', async () => {
    wp().steps = []
    for (const locale of ['ko', 'en', 'ja'] as const) {
      i18n.global.locale.value = locale
      const w = await mountTab(target())
      expect(planLine(w).classes()).not.toContain('is-empty')
      expect(planLine(w).attributes('data-empty')).toBe('false')
      expect(planLine(w).get('[data-test="inspector-slot-name"]').text()).toBe(i18n.global.t('gui.inspector.workPlan.slot.plan'))
      for (const name of ['inspector-slot-open', 'inspector-slot-history', 'document-io-upload', 'document-io-download']) {
        expect(planLine(w).find(`[data-test="${name}"]`).exists(), name).toBe(true)
      }
      const progress = el(w, 'work-plan-progress')
      expect(progress.classes()).toContain('is-empty')
      expect(progress.attributes('data-cards')).toBe('0')
      expect(progress.attributes('aria-hidden')).toBe('true')
      expect(progress.text()).toBe('')
      const text = el(w, 'inspector-work-plan').text()
      for (const old of OLD_NO_CARDS) expect(text).not.toContain(old)
      expect(paneState(w).exists()).toBe(false)
      w.unmount()
      wrapper = null
    }
    i18n.global.locale.value = 'ko'
    expect(i18n.global.te('gui.inspector.workPlan.no_cards')).toBe(false)
  })

  it('(c) a failed read: the error with a retry, no dashed block; the plan line still opens', async () => {
    refusals.push(axiosError(500, { detail: 'boom' }))
    const w = await mountTab(target())
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(el(w, 'inspector-pane-retry').exists()).toBe(true)
    expect(w.find('[data-test="work-plan-progress"].is-empty').exists()).toBe(false)
    expect(el(w, 'work-plan-progress').exists()).toBe(false)
    await planLine(w).get('[data-test="inspector-slot-open"]').trigger('click')
    await flushPromises()
    expect(wpOpened()).toEqual([WP_DOC])
  })

  it('the plan line has no ✎ (one way in: the document icon / the name)', async () => {
    const w = await mountTab(target())
    expect(planLine(w).find('[data-test="inspector-slot-edit"]').exists()).toBe(false)
    expect(planLine(w).find('[data-test="inspector-slot-open"]').exists()).toBe(true)
  })
})

// --- Refusals ----------------------------------------------------------------------------------------

describe('WP summary — refusals (flowgate.v02.0048 T#13)', () => {
  it('409 wp_unreadable: the reason with a retry; the plan line still opens the document', async () => {
    Object.assign(wp(), { unreadable: true, revision: 4 })
    const w = await mountTab(target())
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(paneState(w).text()).toContain('이 작업계획은 표로 읽을 수 없습니다. 문서를 열어 원문을 확인하세요.')
    expect(el(w, 'work-plan-progress').exists()).toBe(false)
    expect(el(w, 'inspector-work-plan').text()).not.toContain('rev 4')
    await planLine(w).get('[data-test="inspector-slot-name"]').trigger('click')
    expect(wpOpened()).toEqual([WP_DOC])
  })

  it.each<[string, unknown, string]>([
    ['403', axiosError(403, { detail: 'Permission denied' }), '작업계획을 볼 권한이 없습니다.'],
    ['404', axiosError(404, { detail: 'Document not found' }), '작업계획 문서를 찾을 수 없습니다.'],
    ['422 not a WP', axiosError(422, { detail: 'not a work plan document.' }), '작업계획 문서가 아닙니다.'],
    ['409 other', axiosError(409, { detail: { code: 'busy' } }), '작업계획을 읽지 못했습니다.'],
    ['500', axiosError(500, { detail: 'boom' }), '작업계획을 읽지 못했습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
  ])('%s: the reason with a retry; the line still opens; the retry reads again', async (_name, refusal, message) => {
    refusals.push(refusal)
    const w = await mountTab(target())
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(paneState(w).text()).toContain(message)
    expect(el(w, 'work-plan-progress').exists()).toBe(false)
    await planLine(w).get('[data-test="inspector-slot-open"]').trigger('click')
    expect(wpOpened()).toHaveLength(1)
    await click(w, 'inspector-pane-retry')
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(paneState(w).exists()).toBe(false)
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 1/4')
  })

  it('a failed read again keeps the last progress with the reason above it', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    refusals.push(networkError())
    await emit('document_explorer_refresh', WP_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 1/4')
  })
})

// --- Events --------------------------------------------------------------------------------------------

describe('WP summary — change events (flowgate.v02.0048 T#13)', () => {
  it('listens to the plan refresh and review events only', () => {
    expect([...WORK_PLAN_EVENTS].sort()).toEqual(['doc_review_status_changed', 'document_explorer_refresh', 'group_view_refresh'])
  })

  it('the plan saved elsewhere (document_explorer_refresh of the WP): the new cards count', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    Object.assign(wp(), { revision: 4, steps: [...planSteps(), ...pairRows('T', 'TR', 3)] })
    await emit('document_explorer_refresh', WP_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 1/5')
  })

  it('group_view_refresh of the WP reads again', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    await emit('group_view_refresh', WP_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
  })

  it('a card started (a document of the same Flow added): the progress follows', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    wp().started = ['D#1', 'T#1']
    await emit('document_explorer_refresh', NEW_TASK_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 2/4')
    expect(el(w, 'work-plan-progress').attributes('data-started')).toBe('2')
  })

  it('the WP reviewed (doc_review_status_changed) reads again', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    await emit('doc_review_status_changed', WP_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
  })

  it('two frames in the window are one read again', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    stream.emit('document_explorer_refresh', { docId: WP_DOC, groupId: FLOW })
    stream.emit('group_view_refresh', { docId: WP_DOC, groupId: FLOW })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(planRead).toHaveBeenCalledTimes(2)
  })

  it('ignored: another document’s review, another Flow’s refresh, events the summary does not hear', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    await emit('doc_review_status_changed', NEW_TASK_DOC)
    await emit('document_explorer_refresh', 'flowgate.v02.0049.0002-T', 'flowgate.v02.0049')
    await emit('group_view_refresh', null, 'flowgate.v02.0049')
    await emit('ai_invoke_finished', WP_DOC)
    await emit('qna_q_registered', WP_DOC)
    expect(planRead).toHaveBeenCalledTimes(1)
  })

  it('a Flow-level refresh without doc_id reads again', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    await emit('group_view_refresh', null)
    expect(planRead).toHaveBeenCalledTimes(2)
  })

  it('a reconnect reads again', async () => {
    vi.useFakeTimers()
    await mountTab(target())
    stream.open()
    stream.reconnect()
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(planRead).toHaveBeenCalledTimes(2)
  })
})

// --- Read-only Flows -------------------------------------------------------------------------------

describe('WP summary — read-only Flows (flowgate.v02.0048 T#13)', () => {
  it.each(['graphRevision', 'conflict', 'graph', 'unassigned'] as const)('%s: the summary shows and the line opens (queries only)', async (flow) => {
    const w = await mountTab(target(flow))
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 1/4')
    await planLine(w).get('[data-test="inspector-slot-open"]').trigger('click')
    expect(wpOpened()).toHaveLength(1)
  })
})

// --- Node change -----------------------------------------------------------------------------------

describe('WP summary — Node change (flowgate.v02.0048 T#13)', () => {
  it('a late answer of the previous Node does not cover the new Node', async () => {
    const release = hold(WP_DOC)
    const w = mount(WorkPlanTab, { props: { target: target() }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    wrapper = w
    await flushPromises()
    await w.setProps({ target: target('v01', OTHER_WP_NODE) })
    await flushPromises()
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 0/1')
    release()
    await flushPromises()
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 0/1')
    await planLine(w).get('[data-test="inspector-slot-open"]').trigger('click')
    expect(wpOpened()).toEqual([OTHER_WP_DOC])
  })
})

// --- In the Frame (real registry) ------------------------------------------------------------------

describe('WP summary in the Frame (real registry)', () => {
  async function mountFrame(selection: FlowGraphSelection, registry = inspectorRegistry) {
    wrapper = mount(DesignerInspector, { props: { selection, registry }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  const tabLabels = (w: W) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((tab) => tab.text())

  // flowgate.v02.0051 stage 2: plan cards with or without a document; the documents tab and the ⇉ 전개 header
  // actions (drawn disabled). flowgate.v02.0074 T#1 (R §1.2): no header meta (the 작성 사람 badge is gone).
  it('registers key workPlan, order 410, plan cards with or without a document: documents tab and header action, no meta', () => {
    const entry = inspectorRegistry.entries.find((candidate) => candidate.key === 'workPlan')!
    expect(entry).toMatchObject({ key: 'workPlan', order: 410, cardKinds: ['plan'], requiresDocument: false })
    expect(entry.tab?.key).toBe('documents')
    expect(entry.headerAction).toBeDefined()
    expect(entry.headerMeta).toBeUndefined()
    expect(entry.ownerships ?? []).toEqual([])
  })

  it.each(['v01', 'graph', 'unassigned', 'conflict'] as const)('WP in a %s Flow: the documents tab shows the summary alone', async (flow) => {
    const w = await mountFrame(planSelection(flow, WP_NODE))
    const section = el(w, 'inspector-work-plan')
    expect(section.exists()).toBe(true)
    // flowgate.v02.0052: the common documents list is not repeated beneath the card.
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
    expect(planRead).toHaveBeenCalledWith(WP_DOC)
    expect(textOf(w, 'work-plan-progress')).toBe('시작한 카드 1/4')
  })

  it('the plan line in the Frame opens the selected plan', async () => {
    const w = await mountFrame(planSelection('v01', WP_NODE))
    await planLine(w).get('[data-test="inspector-slot-open"]').trigger('click')
    await flushPromises()
    expect(wpOpened()).toEqual([WP_DOC])
    expect(opened()).toEqual([])
  })

  it('a TR card: no summary, no read; the tab row is the same as without the registration', async () => {
    const selection = selectionOf(PLAN_FLOWS.v01, V01_TASK_NODE.id)
    const w = await mountFrame(selection)
    expect(el(w, 'inspector-work-plan').exists()).toBe(false)
    expect(planRead).not.toHaveBeenCalled()
    const withSummary = tabLabels(w)
    w.unmount()
    const modules = registrationModules(inspectorRegistry.entries.filter((entry) => entry.key !== 'workPlan'))
    const w2 = await mountFrame(selection, createInspectorRegistry(modules))
    expect(tabLabels(w2)).toEqual(withSummary)
  })

  // flowgate.v02.0051 stage 2 (0051-R §2.3): a WP Node without a document keeps the card's shape — the two
  // lines empty, nothing read, the header actions drawn disabled.
  it('a WP Node without a document: the one line empty, no read, no status line, the deploy actions disabled', async () => {
    const w = await mountFrame(planSelection('v01', WP_EMPTY_NODE))
    const section = el(w, 'inspector-work-plan')
    expect(section.exists()).toBe(true)
    expect(section.attributes('data-has-document')).toBe('false')
    expect(section.findAll('[data-test="inspector-slot"]').map((line) => line.attributes('data-empty'))).toEqual(['true'])
    expect(planRead).not.toHaveBeenCalled()
    expect(el(w, 'work-plan-status').exists()).toBe(false)
    expect(el(w, 'work-plan-deploy').attributes('disabled')).toBeDefined()
  })

  it('a failed WP read stays in the summary section', async () => {
    planRead.mockRejectedValue(axiosError(500, { detail: 'boom' }))
    const w = await mountFrame(planSelection('v01', WP_NODE))
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
  })

  it('the WP card has the same tabs with or without the summary (it only adds to the documents tab)', async () => {
    const w = await mountFrame(planSelection('v01', WP_NODE))
    const withSummary = tabLabels(w)
    w.unmount()
    const modules = registrationModules(inspectorRegistry.entries.filter((entry) => entry.key !== 'workPlan'))
    const w2 = await mountFrame(planSelection('v01', WP_NODE), createInspectorRegistry(modules))
    expect(tabLabels(w2)).toEqual(withSummary)
    expect(el(w2, 'inspector-work-plan').exists()).toBe(false)
  })

  it('no Step editing in the WP summary; the only operations are the slot line’s (name / history / open)', async () => {
    const w = await mountFrame(planSelection('v01', WP_NODE))
    const section = el(w, 'inspector-work-plan')
    for (const word of ['재계획', '⟳', '위임', '메모', 'AI 전달', '연동 전']) expect(section.text()).not.toContain(word)
    expect(section.findAll('input, select, textarea').length).toBe(0)
    // flowgate.v02.0053 T#3 / T#5: the name opens the plan (a button), then the history button (0056 F1) and the
    // document icon — and nothing after the lines. flowgate.v02.0074 T#1 (R §1.3): no ✎ (one way in).
    // flowgate.v02.0068 T#1 (D §0-1 / §6.1): the plan's document line has [업로드][다운로드] before its buttons.
    expect(section.findAll('button').map((button) => button.attributes('data-test'))).toEqual(['inspector-slot-name', 'document-io-upload', 'document-io-download', 'inspector-slot-history', 'inspector-slot-open'])
    // flowgate.v02.0051 stage 2: the v7 lines — the plan with its state; flowgate.v02.0072 T#3: the 전개 line is gone.
    // flowgate.v02.0053 T#5: no type code in a name.
    expect(section.findAll('[data-test="inspector-slot-name"]').map((name) => name.text())).toEqual(['작업계획'])
    expect(section.findAll('[data-test="inspector-slot-state"]').map((state) => state.text())).toEqual(['승인됨'])
    expect(section.text()).not.toContain('전개')
  })
})
