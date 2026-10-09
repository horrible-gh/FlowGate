/**
 * flowgate.v02.0048 T#13 — the WP summary with `@/features/document` mocked as a small server that also refuses
 * (D#1 §12.1): the read view of a plan (WP rev, totals, progress, provider assignment, one row per card with
 * its started mark, where the plan is edited), ↗ 열기 into the v0.1 screen (new tab, noopener, doc_id only),
 * the refusals — 409 wp_unreadable (its revision, ↗ 열기 for the raw view), 403, 404, 422, 500, network — and
 * the retry, the plan's change events (a save / restore / fill of the plan: document_explorer_refresh /
 * group_view_refresh; a review: doc_review_status_changed; a document of the same Flow added or updated: a card
 * started; frames of other Flows and other review changes ignored; a reconnect), the read-only Flows and a Node
 * change (a late answer of the previous Node is dropped). Last, the real registry: the summary on WP cards in
 * the documents tab above the documents list in every ownership, none on other cards or a WP Node without a
 * document (same tab row), a failed read leaves the documents tab as it is, and no undecided wording.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import WorkPlanTab from '@/features/designer/inspector/features/workPlan/WorkPlanTab.vue'
import { WORK_PLAN_EVENTS } from '@/features/designer/inspector/features/workPlan/useWorkPlan'
import { createInspectorRegistry, inspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
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
let opened: unknown[][]

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
  opened = []
  vi.spyOn(window, 'open').mockImplementation((...args: unknown[]) => {
    opened.push(args)
    return null
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
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
const rows = (w: W) =>
  w.findAll('[data-test="work-plan-card"]').map((row) => ({
    card: row.attributes('data-card'),
    started: row.attributes('data-started'),
    text: row.text(),
  }))
const paneState = (w: W) => el(w, 'inspector-work-plan').find('[data-test="inspector-pane-state"]')

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

describe('WP summary — reading (flowgate.v02.0048 T#13)', () => {
  it('one read of the plan: WP rev, totals, progress, assignment, one row per card, the edit line and ↗ 열기', async () => {
    const w = await mountTab(target())
    expect(planRead).toHaveBeenCalledTimes(1)
    expect(planRead).toHaveBeenCalledWith(WP_DOC)
    expect(el(w, 'inspector-work-plan').attributes('data-status')).toBe('ready')
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 3')
    expect(textOf(w, 'work-plan-totals')).toBe('설계 2장 · 작업 2세트 · Step 6개')
    expect(textOf(w, 'work-plan-progress')).toBe('진행 1/4 카드 시작 · Step 6개')
    expect(textOf(w, 'work-plan-assignments')).toBe('공급자 배정: Claude 3 · Codex 2')
    expect(textOf(w, 'work-plan-unassigned')).toBe('공급자를 정하지 않은 Step 1개')
    expect(rows(w)).toEqual([
      { card: 'D#1', started: 'true', text: 'D#1Claude시작됨' },
      { card: 'D#2', started: 'false', text: 'D#2공급자 미정' },
      { card: 'T#1', started: 'false', text: 'T#1 → TR#1Codex검수 1회' },
      { card: 'T#2', started: 'false', text: 'T#2 → TR#2Claude' },
    ])
    expect(textOf(w, 'work-plan-edit')).toBe('Step 편집은 ↗ 열기의 작업계획 편집기에서 합니다.')
    expect(textOf(w, 'work-plan-open')).toBe('↗ 열기')
    expect(el(w, 'work-plan-open').attributes('disabled')).toBeUndefined()
    expect(paneState(w).exists()).toBe(false)
  })

  it('↗ 열기 opens the plan in the v0.1 screen in a new tab: noopener, doc_id only; nothing else is called', async () => {
    const w = await mountTab(target())
    await click(w, 'work-plan-open')
    expect(opened).toEqual([[`/main/?doc=${encodeURIComponent(WP_DOC)}`, '_blank', 'noopener']])
    expect(planRead).toHaveBeenCalledTimes(1)
  })

  it('a finally confirmed plan: the lock line, ↗ 열기 still there', async () => {
    Object.assign(wp(), { editable: false, lockedReason: 'final_approved' })
    const w = await mountTab(target())
    expect(textOf(w, 'work-plan-edit')).toBe('최종 확인된 계획이라 편집할 수 없습니다.')
    expect(el(w, 'work-plan-open').exists()).toBe(true)
  })

  it('a plan locked by its state: the generic lock line', async () => {
    Object.assign(wp(), { editable: false, lockedReason: 'status' })
    const w = await mountTab(target())
    expect(textOf(w, 'work-plan-edit')).toBe('지금 상태에서는 계획을 편집할 수 없습니다.')
  })

  it('a server-assembled card and a fully assigned plan: no unassigned line', async () => {
    wp().steps = [stepRow('AC', 1, { locked: true, locked_reason: 'server_assembled', origin: 'system' })]
    planRead.mockImplementationOnce(async () =>
      toWorkPlanView(workPlanDocument({ steps: wp().steps, unassigned_step_count: 0, assignment_summary: [], totals: { design_sheets: 0, work_sets: 0, steps: 1 } }, [])),
    )
    const w = await mountTab(target())
    expect(rows(w)).toEqual([{ card: 'AC#1', started: 'false', text: 'AC#1서버 구성' }])
    expect(el(w, 'work-plan-unassigned').exists()).toBe(false)
    expect(el(w, 'work-plan-assignments').exists()).toBe(false)
  })

  it('a plan without Steps: the empty line, progress 0/0', async () => {
    wp().steps = []
    const w = await mountTab(target())
    expect(textOf(w, 'work-plan-empty')).toBe('계획에 Step이 없습니다.')
    expect(textOf(w, 'work-plan-progress')).toBe('진행 0/0 카드 시작 · Step 0개')
    expect(rows(w)).toEqual([])
  })

  it('the English and Japanese wording', async () => {
    i18n.global.locale.value = 'en'
    let w = await mountTab(target())
    expect(textOf(w, 'work-plan-progress')).toBe('Progress: 1/4 cards started · 6 Step(s)')
    expect(textOf(w, 'work-plan-open')).toBe('↗ Open')
    w.unmount()
    i18n.global.locale.value = 'ja'
    w = await mountTab(target())
    expect(textOf(w, 'work-plan-progress')).toBe('進行 1/4 カード開始 · Step 6件')
  })
})

// --- Refusals ----------------------------------------------------------------------------------------

describe('WP summary — refusals (flowgate.v02.0048 T#13)', () => {
  it('409 wp_unreadable: the reason, the revision it answered, ↗ 열기 for the raw view', async () => {
    Object.assign(wp(), { unreadable: true, revision: 4 })
    const w = await mountTab(target())
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(paneState(w).text()).toContain('이 작업계획은 표로 읽을 수 없습니다. ↗ 열기에서 원문을 확인하세요.')
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 4')
    expect(el(w, 'work-plan-card').exists()).toBe(false)
    expect(el(w, 'work-plan-edit').exists()).toBe(false)
    await click(w, 'work-plan-open')
    expect(opened).toEqual([[`/main/?doc=${encodeURIComponent(WP_DOC)}`, '_blank', 'noopener']])
  })

  it.each<[string, unknown, string]>([
    ['403', axiosError(403, { detail: 'Permission denied' }), '작업계획을 볼 권한이 없습니다.'],
    ['404', axiosError(404, { detail: 'Document not found' }), '작업계획 문서를 찾을 수 없습니다.'],
    ['422 not a WP', axiosError(422, { detail: 'not a work plan document.' }), '작업계획 문서가 아닙니다.'],
    ['409 other', axiosError(409, { detail: { code: 'busy' } }), '작업계획을 읽지 못했습니다.'],
    ['500', axiosError(500, { detail: 'boom' }), '작업계획을 읽지 못했습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
  ])('%s: the reason with a retry; ↗ 열기 works; the retry reads again', async (_name, refusal, message) => {
    refusals.push(refusal)
    const w = await mountTab(target())
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(paneState(w).text()).toContain(message)
    expect(el(w, 'work-plan-rev').exists()).toBe(false)
    await click(w, 'work-plan-open')
    expect(opened).toHaveLength(1)
    await click(w, 'inspector-pane-retry')
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(paneState(w).exists()).toBe(false)
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 3')
  })

  it('a failed read again keeps the last plan with the reason above it', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    refusals.push(networkError())
    await emit('document_explorer_refresh', WP_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 3')
    expect(rows(w)).toHaveLength(4)
  })
})

// --- Events --------------------------------------------------------------------------------------------

describe('WP summary — change events (flowgate.v02.0048 T#13)', () => {
  it('listens to the plan refresh and review events only', () => {
    expect([...WORK_PLAN_EVENTS].sort()).toEqual(['doc_review_status_changed', 'document_explorer_refresh', 'group_view_refresh'])
  })

  it('the plan saved elsewhere (document_explorer_refresh of the WP): the new revision and Steps', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    Object.assign(wp(), { revision: 4, steps: [...planSteps(), ...pairRows('T', 'TR', 3)] })
    await emit('document_explorer_refresh', WP_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 4')
    expect(rows(w).map((row) => row.card)).toEqual(['D#1', 'D#2', 'T#1', 'T#2', 'T#3'])
    expect(textOf(w, 'work-plan-progress')).toBe('진행 1/5 카드 시작 · Step 8개')
  })

  it('group_view_refresh of the WP reads again', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    wp().revision = 5
    await emit('group_view_refresh', WP_DOC)
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 5')
  })

  it('a card started (a document of the same Flow added): the progress and the started marks', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    wp().started = ['D#1', 'T#1']
    await emit('document_explorer_refresh', NEW_TASK_DOC)
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(textOf(w, 'work-plan-progress')).toBe('진행 2/4 카드 시작 · Step 6개')
    expect(rows(w).filter((row) => row.started === 'true').map((row) => row.card)).toEqual(['D#1', 'T#1'])
  })

  it('the WP reviewed (doc_review_status_changed): the edit line follows the server', async () => {
    vi.useFakeTimers()
    const w = await mountTab(target())
    stream.open()
    Object.assign(wp(), { editable: false, lockedReason: 'final_approved' })
    await emit('doc_review_status_changed', WP_DOC)
    expect(textOf(w, 'work-plan-edit')).toBe('최종 확인된 계획이라 편집할 수 없습니다.')
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
  it.each(['graphRevision', 'conflict', 'graph', 'unassigned'] as const)('%s: the summary and ↗ 열기 work (queries only)', async (flow) => {
    const w = await mountTab(target(flow))
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 3')
    expect(el(w, 'work-plan-open').attributes('disabled')).toBeUndefined()
    await click(w, 'work-plan-open')
    expect(opened).toHaveLength(1)
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
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 0')
    release()
    await flushPromises()
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 0')
    expect(rows(w).map((row) => row.card)).toEqual(['D#1'])
    await click(w, 'work-plan-open')
    expect(opened).toEqual([[`/main/?doc=${encodeURIComponent(OTHER_WP_DOC)}`, '_blank', 'noopener']])
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

  it('registers key workPlan, order 410, plan cards with a document, the documents tab only', () => {
    const entry = inspectorRegistry.entries.find((candidate) => candidate.key === 'workPlan')!
    expect(entry).toMatchObject({ key: 'workPlan', order: 410, cardKinds: ['plan'], requiresDocument: true })
    expect(entry.tab?.key).toBe('documents')
    expect(entry.headerAction).toBeUndefined()
    expect(entry.headerMeta).toBeUndefined()
    expect(entry.ownerships ?? []).toEqual([])
  })

  it.each(['v01', 'graph', 'unassigned', 'conflict'] as const)('WP in a %s Flow: the documents tab shows the summary above the documents list', async (flow) => {
    const w = await mountFrame(planSelection(flow, WP_NODE))
    const section = el(w, 'inspector-work-plan')
    expect(section.exists()).toBe(true)
    const documents = w.find('[data-test="inspector-documents"]')
    expect(documents.exists()).toBe(true)
    // order 410 < 500: stacked above.
    expect(section.element.compareDocumentPosition(documents.element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(planRead).toHaveBeenCalledWith(WP_DOC)
    expect(textOf(w, 'work-plan-rev')).toBe('WP rev 3')
  })

  it('↗ 열기 in the Frame opens the selected plan', async () => {
    const w = await mountFrame(planSelection('v01', WP_NODE))
    await click(w, 'work-plan-open')
    expect(opened).toEqual([[`/main/?doc=${encodeURIComponent(WP_DOC)}`, '_blank', 'noopener']])
  })

  it.each([
    ['a TR card', selectionOf(PLAN_FLOWS.v01, V01_TASK_NODE.id)],
    ['a WP Node without a document', planSelection('v01', WP_EMPTY_NODE)],
  ])('%s: no summary, no read; the tab row is the same as without the registration', async (_name, selection) => {
    const w = await mountFrame(selection)
    expect(el(w, 'inspector-work-plan').exists()).toBe(false)
    expect(planRead).not.toHaveBeenCalled()
    const withSummary = tabLabels(w)
    w.unmount()
    const modules = registrationModules(inspectorRegistry.entries.filter((entry) => entry.key !== 'workPlan'))
    const w2 = await mountFrame(selection, createInspectorRegistry(modules))
    expect(tabLabels(w2)).toEqual(withSummary)
  })

  it('a failed WP read leaves the documents list and the header as they are', async () => {
    planRead.mockRejectedValue(axiosError(500, { detail: 'boom' }))
    const w = await mountFrame(planSelection('v01', WP_NODE))
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(true)
    expect(w.find('[data-test="inspector-documents"] [data-test="inspector-pane-state"][data-state="error"]').exists()).toBe(false)
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

  it('no undecided wording or Step editing in the WP summary (D#1 §8.4)', async () => {
    const w = await mountFrame(planSelection('v01', WP_NODE))
    const section = el(w, 'inspector-work-plan')
    for (const word of ['전개', '재계획', '⇉', '⟳', '위임', '메모', 'AI 전달', '연동 전']) expect(section.text()).not.toContain(word)
    expect(section.findAll('input, select, textarea').length).toBe(0)
    expect(section.findAll('button').map((button) => button.attributes('data-test'))).toEqual(['work-plan-open'])
  })
})
