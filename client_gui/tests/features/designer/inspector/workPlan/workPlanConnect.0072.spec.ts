/**
 * flowgate.v02.0072 T#3 — the WP card's 「작업계획」 line connected to the v0.2 work plan edit window (T#1) and its Step
 * window (T#2). The real Frame (DesignerInspector, real registry) and the real windows host (WorkPlanEditWindows) are
 * mounted; `@shared/api` is the boundary: the plan's GET / PUT and the Flow's AI run read answer, every other read of
 * the Frame's features is offline.
 *
 * - the documents area is one line; its name, document icon and ✎ open the one edit window of the plan (no document
 *   window); asking again brings it back (minimized → restored), no second window;
 * - Step ✎ → Step window → [저장] applies to the parent (dirty, no PUT) → parent [저장] is one PUT of the whole body
 *   with base_revision_no → the Designer is asked to read the Flow again; a card moved before the Step [저장] keeps
 *   the Step's edit on its own card; a 409 keeps the applied edit and a reload reads the server again; a 422 field
 *   error stays on its row;
 * - the opener's verdict: a revision view opens it read only (no ✎ on the line), the Flow's run lock locks it, and
 *   the window follows the Flow's verdict when it changes — also when another card of the Flow is selected;
 * - TR0012 rev1: through the real Center column (its Dock and Frame) the open window follows its own Flow's run as the
 *   session's run marks tell it, whatever is selected (no card, a card of another Flow): a run started, paused or
 *   stopping locks the parent's inputs and [저장] and the Step window's inputs, [저장] and attachment together, nothing
 *   is sent meanwhile and both drafts stay; the run's end frees them. A run locked when the window opened that ends
 *   after the selection left frees it too; a run status not confirmed (the Flow's tab closed) or the Designer left
 *   keeps it locked;
 * - a WP card without its plan document: the line's [작성] opens the write window (no edit window), and once the
 *   Canvas shows the document the line opens it;
 * - signing out closes the edit windows.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { computed, defineComponent, h, reactive, shallowRef, type ShallowRef } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import { editLockOf, NO_RUN_MARKS } from '@/features/designer/center/run/flowRunLock'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import type { InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import { forgetWorkPlanOpeners } from '@/features/designer/inspector/openDocument'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'
import { closeAllDocumentWindows, documentWindowList, slotWriteWindowList } from '@/features/documentWindow'
import { closeAllWorkPlanEditWindows, WorkPlanEditWindows, workPlanEditWindowList } from '@/features/workPlanWindow'
import { workPlanStepWindowList } from '@/features/workPlanWindow/workPlanStepEditWindows'
import { setCurrentUser } from '@/core/auth/currentUser'
import { prepareInspectorI18n, selectionOf } from '../support/inspectorSupport'
import { DOC, FLOW, readAnswer } from '../../../workPlanWindow/workPlanFixtures'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), postFormRequest: vi.fn() }))
vi.mock('@shared/api', async (original) => ({ ...(await original<typeof import('@shared/api')>()), ...api }))

const i18n = prepareInspectorI18n()

const WP = `/api/v1/documents/${encodeURIComponent(DOC)}/work-plan`
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
const reject = (status: number, data: Record<string, unknown>) => ({ isAxiosError: true, response: { status, data } })

let answer: Record<string, any>
let lastBody: Record<string, any> | null
let intents: InspectorHostIntent[]
let frame: VueWrapper<any> | null = null
let host: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllWorkPlanEditWindows()
  closeAllDocumentWindows()
  forgetWorkPlanOpeners()
  for (const mock of Object.values(api)) mock.mockReset()
  answer = readAnswer()
  lastBody = null
  intents = []
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === WP) return { data: copy(answer) }
    if (path === '/api/v1/ai-invoke/active') return { data: { active: false } }
    return offline()
  })
  api.putRequest.mockImplementation(async (path: string, payload: any) => {
    if (path !== WP) return offline()
    lastBody = copy(payload.body)
    for (const s of lastBody!.steps) if (!s.card_id) s.card_id = 'new_' + (s.pair_role === 'result' ? s.pair_key : s.key)
    answer = { ...answer, body: lastBody, revision_no: payload.base_revision_no + 1 }
    return { data: { ok: true, ...copy(answer) } }
  })
  api.postRequest.mockImplementation(offline)
  api.patchRequest.mockImplementation(offline)
  api.postFormRequest.mockImplementation(offline)
})

afterEach(() => {
  frame?.unmount()
  host?.unmount()
  frame = null
  host = null
  closeAllWorkPlanEditWindows()
  closeAllDocumentWindows()
  setCurrentUser(null)
  document.body.innerHTML = ''
})

// --- The WP card on the Canvas -----------------------------------------------------------------------

const line = (slot: string, docType: string, docId: string | null): FlowGraphCardDocument => ({
  slot,
  docType,
  author: slot === 'plan' ? 'human' : 'run',
  docId,
  title: slot === 'plan' ? '작업계획 — 설계 1장 · 작업 2세트' : '전개',
  status: 'open',
  docReviewStatus: docId ? 'pending_review' : null,
})

function card(id: string, docType: string, lines: FlowGraphCardDocument[], kind = 'plan'): FlowGraphNode {
  const first = lines.find((item) => item.author === 'human' && item.docId) ?? null
  return {
    id,
    kind,
    title: '작업계획 카드',
    state: 'draft',
    docType,
    x: 0,
    y: 0,
    structureEditable: true,
    definition: { nodeType: 'document', docType, paletteItemId: null, label: '작업계획 카드', assignee: null, rootBound: false },
    document: first ? { docId: first.docId!, typeCode: first.docType, seq: 6, title: first.title, docReviewStatus: 'pending_review', status: 'open', supersedes: [] } : null,
    cardDocuments: lines,
  }
}

const WP_CARD = card('gn_wp', 'WP', [line('plan', 'WP', DOC), line('deploy', 'M', `${FLOW}.0007-M`)])
const WP_EMPTY = card('gn_wp', 'WP', [line('plan', 'WP', null), line('deploy', 'M', null)])
const TASK_CARD = card('gn_task', 'T', [line('instruction', 'T', `${FLOW}.0009-T`), line('report', 'TR', `${FLOW}.0010-TR`)], 'task')

function graphOf(nodes: FlowGraphNode[], viewMode = 'draft', flowId = FLOW): FlowGraph {
  return {
    flowId,
    nodes,
    edges: [],
    ownership: 'graph',
    viewMode,
    draft: { version: 1, editable: viewMode === 'draft', confirmable: true, blockedReason: null, baseRevisionNo: null },
  } as FlowGraph
}

function selection(
  node: FlowGraphNode,
  options: { viewMode?: string; editLock?: 'run' | 'unknown' | null; nodes?: FlowGraphNode[]; flowId?: string } = {},
): FlowGraphSelection {
  const graph = graphOf(options.nodes ?? [node], options.viewMode, options.flowId)
  return { ...selectionOf(graph, node.id, 'flowgate'), editLock: options.editLock ?? null }
}

async function mountAll(selected: FlowGraphSelection) {
  host = mount(WorkPlanEditWindows, { global: { plugins: [i18n] }, attachTo: document.body })
  frame = mount(DesignerInspector, {
    props: { selection: selected },
    attrs: { onHost: (intent: InspectorHostIntent) => intents.push(intent) },
    global: { plugins: [i18n, createPinia()] },
    attachTo: document.body,
  })
  await flushPromises()
  return frame
}

// --- Helpers -------------------------------------------------------------------------------------------

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const planLine = () => q('inspector-work-plan')!.querySelector<HTMLElement>('[data-test="inspector-slot"][data-slot="plan"]')!
const row = (id: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-step"][data-row="${id}"]`)!
const cardBox = (id: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-card"][data-card="${id}"]`)!
const child = (index = 0) => document.body.querySelector<HTMLElement>(`[data-test="wp-step-window"][data-window="${workPlanStepWindowList()[index].id}"]`)!
const wpOpened = () => workPlanEditWindowList().map((win) => win.docId)
const wpGets = () => api.getRequest.mock.calls.filter(([path]) => path === WP).length
const disabled = (node: HTMLElement | null) => (node as HTMLButtonElement | HTMLInputElement).disabled

async function click(node: HTMLElement | null) {
  expect(node).toBeTruthy()
  node!.click()
  await flushPromises()
}

async function type(node: HTMLElement | null, value: string) {
  expect(node).toBeTruthy()
  ;(node as HTMLInputElement).value = value
  node!.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

async function openFromLine(button = 'inspector-slot-name') {
  await click(planLine().querySelector<HTMLElement>(`[data-test="${button}"]`))
}

// --- Opening ---------------------------------------------------------------------------------------------

describe('the 작업계획 line opens the work plan edit window (flowgate.v02.0072 T#3)', () => {
  it('one document line; name, document icon and ✎ open the one edit window of the plan — never the document window', async () => {
    await mountAll(selection(WP_CARD))
    const lines = q('inspector-work-plan')!.querySelectorAll('[data-test="inspector-slot"]')
    expect([...lines].map((item) => item.querySelector('[data-test="inspector-slot-name"]')!.textContent!.trim())).toEqual(['작업계획'])
    const before = wpGets()
    await openFromLine('inspector-slot-name')
    expect(wpOpened()).toEqual([DOC])
    expect(q('wp-edit-window')!.dataset.doc).toBe(DOC)
    expect(wpGets()).toBe(before + 1)
    for (const button of ['inspector-slot-open', 'inspector-slot-edit']) {
      await openFromLine(button)
      expect(wpOpened(), button).toEqual([DOC])
    }
    expect(document.body.querySelectorAll('[data-test="wp-edit-window"]')).toHaveLength(1)
    expect(documentWindowList()).toEqual([])
    // A minimized window comes back on the next request.
    workPlanEditWindowList()[0].minimized = true
    await openFromLine('inspector-slot-open')
    expect(workPlanEditWindowList()[0].minimized).toBe(false)
    expect(workPlanEditWindowList()).toHaveLength(1)
  })

  it('the window tells the plan with the card line’s title; the window’s own read wins once it answers', async () => {
    await mountAll(selection(WP_CARD))
    await openFromLine()
    expect(workPlanEditWindowList()[0].request).toMatchObject({ docId: DOC, projectId: 'flowgate', groupId: FLOW, title: '작업계획 — 설계 1장 · 작업 2세트' })
    expect(q('wp-edit-heading')!.textContent).toContain('작업계획 — 설계 1장 · 작업 2세트')
    expect(q('wp-edit-revision')!.textContent).toBe('r2')
  })
})

// --- Step window → parent → PUT ----------------------------------------------------------------------

describe('Step window → parent dirty → parent save and read again (flowgate.v02.0072 T#3)', () => {
  it('a Step [저장] only marks the parent; the parent [저장] is one PUT and the Designer reads the Flow again', async () => {
    await mountAll(selection(WP_CARD))
    await openFromLine()
    await click(q('wp-edit-step-edit', row('c_t1|main')))
    expect(workPlanStepWindowList()).toHaveLength(1)
    await type(q('wp-step-text', child()), '인스펙터에서 연 단계')
    await click(q('wp-step-save', child()))
    expect(workPlanStepWindowList()).toHaveLength(0)
    expect(api.putRequest).not.toHaveBeenCalled()
    expect(q('wp-edit-dirty')).not.toBeNull()
    intents.length = 0
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    expect(api.putRequest.mock.calls[0][1]).toMatchObject({ base_revision_no: 2 })
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_text).toBe('인스펙터에서 연 단계')
    expect(lastBody!.x_origin_note).toEqual({ kept: true })
    expect(q('wp-edit-dirty')).toBeNull()
    expect(q('wp-edit-revision')!.textContent).toBe('r3')
    expect(intents).toContainEqual({ kind: 'refresh-flow', flowId: FLOW })
  })

  it('a card moved while its Step window is open: the Step [저장] still edits its own card', async () => {
    await mountAll(selection(WP_CARD))
    await openFromLine()
    await click(q('wp-edit-step-edit', row('c_t2|main')))
    await type(q('wp-step-text', child()), 'T#2 카드의 지시')
    await click(q('wp-edit-card-up', cardBox('c_t2')))
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    const steps = lastBody!.steps as any[]
    const moved = steps.find((s) => s.card_id === 'c_t2' && s.pair_role === 'instruction')
    expect(moved.key).toBe('T#1')
    expect(moved.pre_instruction_text).toBe('T#2 카드의 지시')
    expect(steps.find((s) => s.card_id === 'c_t1' && s.pair_role === 'instruction').pre_instruction_text).toBe('사전 지시')
  })

  it('409: the applied Step edit stays; reading again takes the server plan', async () => {
    await mountAll(selection(WP_CARD))
    await openFromLine()
    await click(q('wp-edit-step-edit', row('c_t1|main')))
    await type(q('wp-step-text', child()), '충돌 전 편집')
    await click(q('wp-step-save', child()))
    api.putRequest.mockRejectedValueOnce(reject(409, { code: 'wp_revision_conflict', updated_by: 'kim', updated_at: '2026-10-19T08:00' }))
    await click(q('wp-edit-save'))
    expect(q('wp-edit-conflict')).not.toBeNull()
    expect(q('wp-edit-dirty')).not.toBeNull()
    answer = { ...answer, revision_no: 5 }
    await click(q('wp-edit-conflict-reload'))
    await click(document.body.querySelectorAll<HTMLElement>('.ui-confirm-actions button')[1])
    expect(q('wp-edit-revision')!.textContent).toBe('r5')
    expect(q('wp-edit-dirty')).toBeNull()
  })

  it('422: a field error stays on its row and the edit is kept', async () => {
    await mountAll(selection(WP_CARD))
    await openFromLine()
    api.putRequest.mockRejectedValueOnce(
      reject(422, { code: 'validation_failed', message: 'invalid', errors: [{ loc: 'steps[3].note', key: 'T#2', code: 'note_too_long', params: { max: 200 }, msg: 'too long' }] }),
    )
    await type(q('wp-edit-step-note', row('c_t2|main')), 'x'.repeat(10))
    await click(q('wp-edit-save'))
    expect(q('wp-edit-failure')?.dataset.kind).toBe('validation')
    expect(q('wp-edit-step-errors', row('c_t2|main'))).not.toBeNull()
    expect(q('wp-edit-dirty')).not.toBeNull()
  })
})

// --- The opener's verdict ------------------------------------------------------------------------------

describe('read only and run lock from the opener (flowgate.v02.0072 T#3)', () => {
  it('a revision view: the line has no ✎, the name opens the window read only and sends nothing', async () => {
    await mountAll(selection(WP_CARD, { viewMode: 'revision' }))
    expect(planLine().querySelector('[data-test="inspector-slot-edit"]')).toBeNull()
    await openFromLine('inspector-slot-name')
    expect(q('wp-edit-lock')!.dataset.reason).toBe('read_only')
    expect(disabled(q('wp-edit-step-note', row('c_t2|main')))).toBe(true)
    expect(disabled(q('wp-edit-save'))).toBe(true)
    expect(disabled(q('wp-edit-step-edit', row('c_t1|main')))).toBe(false)
    await click(q('wp-edit-step-edit', row('c_t1|main')))
    expect(disabled(q('wp-step-text', child()))).toBe(true)
    expect(disabled(q('wp-step-save', child()))).toBe(true)
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('the Flow’s run lock locks the window; when the run ends the window may edit again', async () => {
    const w = await mountAll(selection(WP_CARD, { editLock: 'run' }))
    await openFromLine('inspector-slot-edit')
    expect(q('wp-edit-lock')!.dataset.reason).toBe('run_locked')
    expect(disabled(q('wp-edit-step-note', row('c_t2|main')))).toBe(true)
    await w.setProps({ selection: selection(WP_CARD, { editLock: null }) })
    await flushPromises()
    expect(q('wp-edit-lock')).toBeNull()
    await type(q('wp-edit-step-note', row('c_t2|main')), '실행 끝난 뒤')
    expect(disabled(q('wp-edit-save'))).toBe(false)
  })

  it('another card of the same Flow selected: the open window follows the Flow’s lock (an unconfirmed run status too)', async () => {
    const nodes = [WP_CARD, TASK_CARD]
    const w = await mountAll(selection(WP_CARD, { nodes }))
    await openFromLine()
    expect(q('wp-edit-lock')).toBeNull()
    await w.setProps({ selection: selection(TASK_CARD, { nodes, editLock: 'unknown' }) })
    await flushPromises()
    expect(q('wp-edit-lock')!.dataset.reason).toBe('run_locked')
    await w.setProps({ selection: selection(TASK_CARD, { nodes, editLock: null }) })
    await flushPromises()
    expect(q('wp-edit-lock')).toBeNull()
  })
})

// --- Empty plan, sign-out --------------------------------------------------------------------------------

describe('a WP card without its plan; sign-out (flowgate.v02.0072 T#3)', () => {
  it('the line says 문서 없음 with [작성] (the write window, no edit window); once the Canvas shows the plan it opens', async () => {
    const w = await mountAll(selection(WP_EMPTY))
    const empty = planLine()
    expect(empty.dataset.empty).toBe('true')
    expect(empty.querySelector('[data-test="inspector-slot-open"]')).toBeNull()
    const write = empty.querySelector<HTMLElement>('[data-test="inspector-slot-action"][data-action="write"]')
    await click(write)
    expect(slotWriteWindowList()).toHaveLength(1)
    expect(wpOpened()).toEqual([])
    await w.setProps({ selection: selection(WP_CARD) })
    await flushPromises()
    await openFromLine('inspector-slot-open')
    expect(wpOpened()).toEqual([DOC])
  })

  it('signing out closes the edit windows (and their Step windows) without asking', async () => {
    setCurrentUser({ username: 'kim', email: null, isAdmin: false })
    await mountAll(selection(WP_CARD))
    await openFromLine()
    await click(q('wp-edit-step-edit', row('c_t1|main')))
    await type(q('wp-step-text', child()), '로그아웃 전')
    expect(workPlanStepWindowList()).toHaveLength(1)
    setCurrentUser(null)
    await flushPromises()
    expect(wpOpened()).toEqual([])
    expect(workPlanStepWindowList()).toHaveLength(0)
    expect(q('wp-edit-window')).toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
  })
})


// --- TR0012 rev1: the open window follows its own Flow's run, whatever is selected ---------------------------------

/** Another Flow of the project, open in the Designer. */
const OTHER_FLOW = 'flowgate.v02.0099'

/** The Flows' runs as the session read them: absent = not confirmed (not read, or the tab closed); null = no run. */
let runs: Record<string, { status: string } | null>
/** What the Center column shows as selected (the active Flow's selected card), or nothing. */
let shown: ShallowRef<FlowGraphSelection | null>

const Empty = defineComponent({ render: () => h('section') })

/**
 * The real Center column (its Dock and the real Frame) over a session that tells the selection and every Flow's run
 * marks: the lock is the run lock rule itself (editLockOf: running / paused / stopping, or a status not confirmed).
 */
async function mountDesigner(selected: FlowGraphSelection | null) {
  shown = shallowRef(selected)
  const lock = (flowId: string) => editLockOf(runs[flowId], flowId in runs)
  const center = {
    canEditNodeTitles: false,
    saveNodeTitle: vi.fn(),
    // As the session's: the selected card carries its Flow's run lock (the Inspector's card settings follow it).
    activeSelection: computed(() => (shown.value ? { ...shown.value, editLock: lock(shown.value.graph.flowId) } : null)),
    activeSelectedNode: computed(() => shown.value?.node ?? null),
    activeGraphLoad: { value: null },
    activeView: { value: null },
    activeNodeSizes: { value: null },
    canvasSize: { value: null },
    state: reactive({ activeFlowId: FLOW as string | null }),
    runMarks: (flowId: string) => ({ ...NO_RUN_MARKS, lock: lock(flowId) }),
    refreshFlow: vi.fn(),
    selectNode: vi.fn(),
  }
  host = mount(WorkPlanEditWindows, { global: { plugins: [i18n] }, attachTo: document.body })
  frame = mount(DesignerCenterColumn, {
    props: { center: center as unknown as DesignerCenterSession, bottomHeight: 300 },
    global: { plugins: [i18n, createPinia()], stubs: { DesignerWorkspace: Empty, DesignerMiniMap: Empty } },
    attachTo: document.body,
  })
  await flushPromises()
  return center
}

async function run(flowId: string, status: string | null | undefined) {
  if (status === undefined) delete runs[flowId]
  else runs[flowId] = status === null ? null : { status }
  await flushPromises()
}

async function select(next: FlowGraphSelection | null) {
  shown.value = next
  await flushPromises()
}

const refFile = (name: string) => ({ doc_id: DOC, filename: '__wp_pre_instruction__T-1__' + name, original_filename: name, content_sha256: 'b'.repeat(64) })

async function attach(name: string) {
  const node = q('wp-step-file', child()) as HTMLInputElement
  Object.defineProperty(node, 'files', { configurable: true, value: [new File(['xy'], name)] })
  node.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}

const noteValue = () => (q('wp-edit-step-note', row('c_t2|main')) as HTMLInputElement).value
const stepText = () => (q('wp-step-text', child()) as HTMLTextAreaElement).value

/** The parent's inputs and the open Step window's inputs, [저장] and attachment, locked by the run or free together. */
function expectStepWindow(locked: boolean, label: string) {
  if (locked) expect(q('wp-edit-lock')?.dataset.reason, label).toBe('run_locked')
  else expect(q('wp-edit-lock'), label).toBeNull()
  expect(disabled(q('wp-edit-step-note', row('c_t2|main'))), label).toBe(locked)
  expect(disabled(q('wp-step-text', child())), label).toBe(locked)
  expect(disabled(q('wp-step-save', child())), label).toBe(locked)
  expect(disabled(q('wp-step-choose', child())), label).toBe(locked)
  expect(disabled(q('wp-step-file', child())), label).toBe(locked)
}

/** The parent alone (no Step window open): its inputs and [저장] (it has an edit to save). */
function expectParent(locked: boolean, label: string) {
  if (locked) expect(q('wp-edit-lock')?.dataset.reason, label).toBe('run_locked')
  else expect(q('wp-edit-lock'), label).toBeNull()
  expect(disabled(q('wp-edit-step-note', row('c_t2|main'))), label).toBe(locked)
  expect(disabled(q('wp-edit-save')), label).toBe(locked)
}

describe('the open window follows its own Flow’s run, whatever is selected (flowgate.v02.0072 TR0012 rev1)', () => {
  beforeEach(() => {
    runs = reactive({})
    api.postFormRequest.mockImplementation(async (_path: string, form: FormData) => ({ data: { ok: true, reference: refFile((form.get('file') as File).name) } }))
  })

  it('no card selected: a run started, paused and stopping locks parent and Step window together, sends nothing and keeps both drafts; its end frees them', async () => {
    await run(FLOW, null)
    await mountDesigner(selection(WP_CARD))
    await openFromLine()
    await type(q('wp-edit-step-note', row('c_t2|main')), '실행 전 부모 편집')
    await click(q('wp-edit-step-edit', row('c_t1|main')))
    await type(q('wp-step-text', child()), '실행 전 단계 편집')
    expectStepWindow(false, 'before the run')

    await select(null)
    expect(q('designer-inspector-empty')).not.toBeNull()
    for (const status of ['running', 'paused', 'stopping']) {
      await run(FLOW, status)
      expectStepWindow(true, status)
      await attach(`${status}.txt`)
      await click(q('wp-step-save', child()))
      expect(workPlanStepWindowList(), status).toHaveLength(1)
      expect(noteValue(), status).toBe('실행 전 부모 편집')
      expect(stepText(), status).toBe('실행 전 단계 편집')
    }
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(api.putRequest).not.toHaveBeenCalled()

    // The run stopped (idle): everything is free again, with the drafts as they were.
    await run(FLOW, 'idle')
    expectStepWindow(false, 'idle')
    expect(noteValue()).toBe('실행 전 부모 편집')
    expect(stepText()).toBe('실행 전 단계 편집')
    await attach('after.txt')
    expect(api.postFormRequest).toHaveBeenCalledTimes(1)
    expect(q('wp-step-file-name', child())!.textContent).toBe('after.txt')
    await click(q('wp-step-save', child()))
    expect(workPlanStepWindowList()).toHaveLength(0)
    expectParent(false, 'idle, the Step applied')

    // A new run locks the parent's [저장] as well: nothing is sent, the edit stays; the run's end lets it save.
    await run(FLOW, 'running')
    expectParent(true, 'running again')
    await click(q('wp-edit-save'))
    expect(api.putRequest).not.toHaveBeenCalled()
    expect(q('wp-edit-dirty')).not.toBeNull()
    await run(FLOW, null)
    expectParent(false, 'no run')
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    const step1 = lastBody!.steps.find((s: any) => s.key === 'T#1')
    expect(step1.pre_instruction_text).toBe('실행 전 단계 편집')
    expect(step1.pre_instruction_attachment.original_filename).toBe('after.txt')
    expect(lastBody!.steps.find((s: any) => s.key === 'T#2').note).toBe('실행 전 부모 편집')
  })

  it('a card of another Flow selected: the window follows its own Flow’s run, not the selected Flow’s', async () => {
    await run(FLOW, null)
    await run(OTHER_FLOW, null)
    await mountDesigner(selection(WP_CARD))
    await openFromLine()
    await click(q('wp-edit-step-edit', row('c_t1|main')))
    await type(q('wp-step-text', child()), '다른 플로우 선택 전')

    await select(selection(TASK_CARD, { flowId: OTHER_FLOW }))
    expect(q('designer-inspector')!.dataset.selected).toBe('true')
    // The selected Flow's run does not lock this window ...
    await run(OTHER_FLOW, 'running')
    expectStepWindow(false, 'the other Flow runs')
    // ... its own Flow's does, started, paused and stopping, and its end frees it.
    for (const status of ['running', 'paused', 'stopping']) {
      await run(FLOW, status)
      expectStepWindow(true, `own Flow ${status}`)
      await attach(`${status}.txt`)
    }
    await run(OTHER_FLOW, null)
    expectStepWindow(true, 'the other Flow ended, its own still stopping')
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(api.putRequest).not.toHaveBeenCalled()
    await run(FLOW, 'idle')
    expectStepWindow(false, 'own Flow idle')
    expect(stepText()).toBe('다른 플로우 선택 전')
  })

  it('a run locked when the window opened that ends after the selection left frees it — no card or another Flow', async () => {
    await run(FLOW, 'running')
    await run(OTHER_FLOW, null)
    await mountDesigner(selection(WP_CARD))
    await openFromLine()
    expectParent(true, 'opened while running')

    await select(null)
    await run(FLOW, 'paused')
    expectParent(true, 'paused, no card')
    await run(FLOW, 'idle')
    expect(q('wp-edit-lock')).toBeNull()
    expect(disabled(q('wp-edit-step-note', row('c_t2|main')))).toBe(false)

    await run(FLOW, 'running')
    await select(selection(TASK_CARD, { flowId: OTHER_FLOW }))
    expect(q('wp-edit-lock')?.dataset.reason).toBe('run_locked')
    await run(FLOW, null)
    expect(q('wp-edit-lock')).toBeNull()
    await type(q('wp-edit-step-note', row('c_t2|main')), '실행 끝난 뒤')
    expect(disabled(q('wp-edit-save'))).toBe(false)
  })

  it('a run status not confirmed (the Flow’s tab closed) or the Designer left keeps the window locked', async () => {
    await run(FLOW, null)
    await mountDesigner(selection(WP_CARD))
    await openFromLine()
    await type(q('wp-edit-step-note', row('c_t2|main')), '확인 전 편집')
    await select(null)
    expectParent(false, 'no run')

    await run(FLOW, undefined)
    expectParent(true, 'not confirmed')
    await click(q('wp-edit-save'))
    await run(FLOW, null)
    expectParent(false, 'confirmed again')

    frame!.unmount()
    frame = null
    await flushPromises()
    expectParent(true, 'the Designer left')
    await click(q('wp-edit-save'))
    expect(api.putRequest).not.toHaveBeenCalled()
    expect(noteValue()).toBe('확인 전 편집')
  })
})
