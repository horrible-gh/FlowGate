/**
 * flowgate.v02.0048 T#6 — the header actions with `@/features/document` mocked as a small server
 * (D#1 §12.1): the calls the buttons make (approve, reject with its reason, v0.1 run = edit,
 * rework), the state read again after each call, the refusals (409 state contention, 423 / 409 AI
 * running, 409 disposed, 403, network) shown as a reason with the state read again, the AI activity
 * and document events read again through the real hub, read-only Flows, an action in flight and a
 * Node change. Last, the real registry puts the bar in the Frame header of review cards only, and
 * no Graph run / retry / gate / expansion / delegation button exists anywhere.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import ActionsBar from '@/features/designer/inspector/features/actions/ActionsBar.vue'
import { ACTIONS_ACTIVITY_EVENTS, ACTIONS_DOCUMENT_EVENTS } from '@/features/designer/inspector/features/actions/useActions'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, V01_GRAPH, V01_TASK_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The calls are mocked; normalization and error reading stay real.
const documentRead = vi.fn()
const activityRead = vi.fn()
const approveCall = vi.fn()
const rejectCall = vi.fn()
const runStart = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    reviewApprove: (...args: unknown[]) => approveCall(...args),
    reviewReject: (...args: unknown[]) => rejectCall(...args),
    reviewRunStart: (...args: unknown[]) => runStart(...args),
    // The documents tab shares the Frame in the registry cases.
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
  }
})

const { toReviewDocument, toReviewTransitionResult, toReviewActivity, toDocumentDetail } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const V01_DOC = V01_TASK_NODE.document!.docId
const V01_FLOW = V01_GRAPH.flowId

// --- The server ------------------------------------------------------------------------------------

interface ServerDoc {
  status: string | null
  typeCode: string
  disposed: boolean
}

const docs = new Map<string, ServerDoc>()
let running = false
/** The next answer of a call is this refusal (once). */
const refusals = new Map<string, unknown>()

const rowOf = (docId: string) => {
  const doc = docs.get(docId)!
  return { doc_id: docId, group_id: docId.split('.').slice(0, 3).join('.'), type_code: doc.typeCode, title: docId, status: 'open', doc_review_status: doc.status, group_disposed: doc.disposed }
}

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })

function refusalOf(call: string): unknown {
  const refusal = refusals.get(call)
  refusals.delete(call)
  return refusal
}

function serve() {
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(rowOf(docId)))
  activityRead.mockImplementation(async (groupId: string) =>
    toReviewActivity(running ? { ok: true, active: true, run_id: 'run_1', status: 'running', group_id: groupId, doc_ref: V01_DOC } : { ok: true, active: false, group_id: groupId }),
  )
  approveCall.mockImplementation(async (docId: string) => {
    const refusal = refusalOf('approve')
    if (refusal) throw refusal
    docs.get(docId)!.status = 'approved'
    const tr = docs.get(docId)!.typeCode === 'TR'
    return toReviewTransitionResult({
      document: rowOf(docId),
      ...(tr ? { tr_commit: { committed: true, commit: 'abcdef0123456789', subject: 'feat(inspector): header actions', skipped_reason: null } } : {}),
    })
  })
  rejectCall.mockImplementation(async (docId: string, reason: string) => {
    const refusal = refusalOf('reject')
    if (refusal) throw refusal
    docs.get(docId)!.status = 'rejected'
    return toReviewTransitionResult({ document: { ...rowOf(docId), rejection_reason: reason } })
  })
  runStart.mockImplementation(async () => {
    const refusal = refusalOf('run')
    if (refusal) throw refusal
    running = true
    return { runId: 'run_1', status: 'running' }
  })
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(rowOf(docId)))
  relationsRead.mockImplementation(async () => [])
}

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  docs.clear()
  refusals.clear()
  running = false
  docs.set(V01_DOC, { status: 'pending_review', typeCode: 'TR', disposed: false })
  for (const mock of [documentRead, activityRead, approveCall, rejectCall, runStart, detailRead, relationsRead]) mock.mockReset()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.useRealTimers()
})

async function mountBar(target: InspectorTarget) {
  wrapper = mount(ActionsBar, { props: { target }, global: { plugins: [i18n, pinia] } })
  await flushPromises()
  return wrapper
}

const v01Target = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...targetOf(SELECTIONS.v01), ...fields })

const button = (w: VueWrapper<any>, name: string) => w.find(`[data-test="actions-${name}"]`)
const shown = (w: VueWrapper<any>) => ['approve', 'reject', 'run', 'rework'].filter((name) => button(w, name).exists())
const disabled = (w: VueWrapper<any>, name: string) => button(w, name).attributes('disabled') !== undefined
const line = (w: VueWrapper<any>) => w.find('[data-test="actions-line"]')

const body = () => document.body
const dialog = () => body().querySelector('[data-test="actions-reject-dialog"]')
const dialogButton = (name: string) => body().querySelector<HTMLButtonElement>(`[data-test="actions-reject-${name}"]`)

async function typeReason(text: string) {
  const area = body().querySelector<HTMLTextAreaElement>('[data-test="actions-reject-reason"] textarea, textarea[data-test="actions-reject-reason"]')!
  area.value = text
  area.dispatchEvent(new Event('input'))
  await flushPromises()
}

// --- Calls -----------------------------------------------------------------------------------------

describe('header actions — calls and state read again (flowgate.v02.0048 T#6)', () => {
  it('a v0.1 TR under review shows approve · reject · ▶ run, read once each', async () => {
    const w = await mountBar(v01Target())
    expect(shown(w)).toEqual(['approve', 'reject', 'run'])
    expect(documentRead).toHaveBeenCalledTimes(1)
    expect(documentRead).toHaveBeenCalledWith(V01_DOC)
    expect(activityRead).toHaveBeenCalledTimes(1)
    expect(activityRead).toHaveBeenCalledWith(V01_FLOW)
    expect(line(w).exists()).toBe(false)
  })

  it('approve calls the API, reads again, and the new state hides the decision; the TR commit is shown', async () => {
    const w = await mountBar(v01Target())
    await button(w, 'approve').trigger('click')
    await flushPromises()
    expect(approveCall).toHaveBeenCalledWith(V01_DOC)
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(shown(w)).toEqual([])
    expect(line(w).attributes('data-kind')).toBe('notice')
    expect(line(w).text()).toBe('TR 커밋 abcdef01 feat(inspector): header actions')
  })

  it('reject opens the reason dialog; an empty reason is not sent; the reason is sent and the dialog closes', async () => {
    const w = await mountBar(v01Target())
    await button(w, 'reject').trigger('click')
    await flushPromises()
    expect(dialog()).not.toBeNull()
    expect(dialogButton('submit')!.disabled).toBe(true)
    await typeReason('   ')
    expect(dialogButton('submit')!.disabled).toBe(true)
    await typeReason('  근거 문서 링크 누락 ')
    expect(dialogButton('submit')!.disabled).toBe(false)
    dialogButton('submit')!.click()
    await flushPromises()
    expect(rejectCall).toHaveBeenCalledWith(V01_DOC, '근거 문서 링크 누락')
    expect(dialog()).toBeNull()
    expect(documentRead).toHaveBeenCalledTimes(2)
    // Rejected: the decision is gone and the rework of the rejected document appears.
    expect(shown(w)).toEqual(['rework'])
  })

  it('cancel closes the dialog without a call', async () => {
    const w = await mountBar(v01Target())
    await button(w, 'reject').trigger('click')
    await flushPromises()
    dialogButton('cancel')!.click()
    await flushPromises()
    expect(dialog()).toBeNull()
    expect(rejectCall).not.toHaveBeenCalled()
  })

  it('"reject, then run again" is two steps: reject, then ↻ rework (action_scope rework)', async () => {
    const w = await mountBar(v01Target())
    await button(w, 'reject').trigger('click')
    await flushPromises()
    await typeReason('테스트 누락')
    dialogButton('submit')!.click()
    await flushPromises()
    await button(w, 'rework').trigger('click')
    await flushPromises()
    expect(runStart).toHaveBeenCalledWith({ groupId: V01_FLOW, docId: V01_DOC, scope: 'rework' })
    // The run now owns the group: read again, every operation waits.
    expect(activityRead).toHaveBeenCalledTimes(3)
    expect(disabled(w, 'rework')).toBe(true)
    expect(line(w).text()).toBe('AI 실행 중이라 바꿀 수 없습니다.')
  })

  it('▶ run starts a v0.1 single run on the document (action_scope edit)', async () => {
    const w = await mountBar(v01Target())
    await button(w, 'run').trigger('click')
    await flushPromises()
    expect(runStart).toHaveBeenCalledWith({ groupId: V01_FLOW, docId: V01_DOC, scope: 'edit' })
    expect(['approve', 'reject', 'run'].every((name) => disabled(w, name))).toBe(true)
  })

  it('while a call waits every operation is disabled and a second click sends nothing', async () => {
    let answer: (value: unknown) => void = () => {}
    approveCall.mockImplementation(() => new Promise((resolve) => (answer = resolve)))
    const w = await mountBar(v01Target())
    await button(w, 'approve').trigger('click')
    await flushPromises()
    expect(w.get('[data-test="inspector-actions"]').attributes('data-busy')).toBe('approve')
    expect(['approve', 'reject', 'run'].every((name) => disabled(w, name))).toBe(true)
    await button(w, 'run').trigger('click')
    expect(runStart).not.toHaveBeenCalled()
    docs.get(V01_DOC)!.status = 'approved'
    answer(toReviewTransitionResult({ document: rowOf(V01_DOC) }))
    await flushPromises()
    expect(approveCall).toHaveBeenCalledTimes(1)
    expect(shown(w)).toEqual([])
  })
})

// --- Refusals --------------------------------------------------------------------------------------

describe('header actions — refusals are shown and the state is read again', () => {
  it('409 state contention: reason shown, read again, the server state wins', async () => {
    const w = await mountBar(v01Target())
    // Someone else decided first: the server already holds approved.
    refusals.set('approve', axiosError(409, { detail: "Invalid review transition: doc_review_status='approved' + action='approve'" }))
    docs.get(V01_DOC)!.status = 'approved'
    await button(w, 'approve').trigger('click')
    await flushPromises()
    expect(line(w).attributes('data-kind')).toBe('failure')
    expect(line(w).text()).toBe('지금 상태에서는 처리할 수 없어 거절되었습니다. 상태를 다시 읽었습니다.')
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(shown(w)).toEqual([])
  })

  it('409 of a server gate (empty body, not the workflow head): reason shown, the buttons stay usable', async () => {
    const w = await mountBar(v01Target())
    refusals.set('approve', axiosError(409, { detail: '본문이 비어 있어 승인할 수 없습니다. 문서 내용을 채운 뒤 다시 승인하십시오.' }))
    await button(w, 'approve').trigger('click')
    await flushPromises()
    expect(line(w).text()).toBe('지금 상태에서는 처리할 수 없어 거절되었습니다. 상태를 다시 읽었습니다.')
    expect(disabled(w, 'approve')).toBe(false)
  })

  it('423 GROUP_AI_RUN_LOCKED: "AI running" reason, activity read again, every operation disabled', async () => {
    const w = await mountBar(v01Target())
    refusals.set('approve', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'x', run_live: true } }))
    running = true
    await button(w, 'approve').trigger('click')
    await flushPromises()
    expect(line(w).text()).toBe('AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.')
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(['approve', 'reject', 'run'].every((name) => disabled(w, name))).toBe(true)
  })

  it('409 run_in_progress on ▶ run: "AI running" reason', async () => {
    const w = await mountBar(v01Target())
    refusals.set('run', axiosError(409, { code: 'run_in_progress', message: 'An AI run is already in progress for this group.' }))
    running = true
    await button(w, 'run').trigger('click')
    await flushPromises()
    expect(line(w).text()).toBe('AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.')
    expect(disabled(w, 'run')).toBe(true)
  })

  it('409 group_disposed: "disposed" reason; the read again knows the group is disposed', async () => {
    const w = await mountBar(v01Target())
    refusals.set('approve', axiosError(409, { detail: `group_disposed: ${V01_DOC} belongs to a disposed group` }))
    docs.get(V01_DOC)!.disposed = true
    await button(w, 'approve').trigger('click')
    await flushPromises()
    expect(line(w).text()).toBe('폐기된 그룹이라 거절되었습니다. 상태를 다시 읽었습니다.')
    expect(['approve', 'reject', 'run'].every((name) => disabled(w, name))).toBe(true)
  })

  it('a refused reject keeps the dialog open with its reason', async () => {
    const w = await mountBar(v01Target())
    await button(w, 'reject').trigger('click')
    await flushPromises()
    await typeReason('사유')
    refusals.set('reject', axiosError(409, { detail: 'Invalid review transition' }))
    dialogButton('submit')!.click()
    await flushPromises()
    expect(dialog()).not.toBeNull()
    expect(body().querySelector('[data-test="actions-reject-failure"]')!.textContent).toBe('지금 상태에서는 처리할 수 없어 거절되었습니다. 상태를 다시 읽었습니다.')
    expect(documentRead).toHaveBeenCalledTimes(2)
    // Opening it again starts clean.
    dialogButton('cancel')!.click()
    await flushPromises()
    await button(w, 'reject').trigger('click')
    await flushPromises()
    expect(body().querySelector('[data-test="actions-reject-failure"]')).toBeNull()
  })

  it.each([
    ['403', axiosError(403, { detail: 'Insufficient permissions' }), '이 조작을 할 권한이 없습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
    ['500', axiosError(500, {}), '조작을 마치지 못했습니다.'],
  ])('%s: reason shown, nothing stays blocked', async (_label, refusal, text) => {
    const w = await mountBar(v01Target())
    refusals.set('approve', refusal)
    await button(w, 'approve').trigger('click')
    await flushPromises()
    expect(line(w).text()).toBe(text)
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(disabled(w, 'approve')).toBe(false)
  })

  it("a refusal answered after the Node changed is not shown on the new Node", async () => {
    let fail: (error: unknown) => void = () => {}
    approveCall.mockImplementation(() => new Promise((_resolve, reject) => (fail = reject)))
    const other = 'flowgate.v02.0048.0013-TR'
    docs.set(other, { status: 'pending_review', typeCode: 'TR', disposed: false })
    const w = await mountBar(v01Target())
    await button(w, 'approve').trigger('click')
    await w.setProps({ target: v01Target({ docId: other, nodeId: `doc:${other}` }) })
    fail(axiosError(409, { detail: 'x' }))
    await flushPromises()
    expect(line(w).exists()).toBe(false)
    expect(shown(w)).toEqual(['approve', 'reject', 'run'])
  })
})

// --- Availability inputs ---------------------------------------------------------------------------

describe('header actions — AI activity, disposed, read only', () => {
  it('an AI run of the group disables every operation with its reason', async () => {
    running = true
    const w = await mountBar(v01Target())
    expect(['approve', 'reject', 'run'].every((name) => disabled(w, name))).toBe(true)
    expect(line(w).attributes('data-kind')).toBe('reason')
    expect(line(w).text()).toBe('AI 실행 중이라 바꿀 수 없습니다.')
    await button(w, 'approve').trigger('click')
    expect(approveCall).not.toHaveBeenCalled()
  })

  it('a disposed group disables every operation', async () => {
    docs.get(V01_DOC)!.disposed = true
    const w = await mountBar(v01Target())
    expect(['approve', 'reject', 'run'].every((name) => disabled(w, name))).toBe(true)
    expect(line(w).text()).toBe('폐기된 그룹이라 바꿀 수 없습니다.')
  })

  it('the read status wins over the Canvas one', async () => {
    docs.get(V01_DOC)!.status = 'rejected'
    const w = await mountBar(v01Target())
    expect(shown(w)).toEqual(['rework'])
  })

  it('a Graph revision view: the decision is shown disabled with the read-only reason, no run', async () => {
    const w = await mountBar(v01Target({ ownership: 'graph', viewMode: 'revision', readOnly: 'revision' }))
    expect(shown(w)).toEqual(['approve', 'reject'])
    expect(disabled(w, 'approve') && disabled(w, 'reject')).toBe(true)
    expect(line(w).text()).toBe('Graph revision 보기에서는 바꿀 수 없습니다. draft 보기로 옮기세요.')
    await button(w, 'approve').trigger('click')
    expect(approveCall).not.toHaveBeenCalled()
  })

  it('the same Node in the draft view: the decision is usable', async () => {
    const w = await mountBar(v01Target({ ownership: 'graph', viewMode: 'draft', readOnly: null }))
    expect(shown(w)).toEqual(['approve', 'reject'])
    expect(disabled(w, 'approve')).toBe(false)
  })

  it('an ownership conflict: disabled with the conflict reason', async () => {
    const w = await mountBar(v01Target({ ownership: 'conflict', viewMode: 'revision', readOnly: 'conflict' }))
    expect(shown(w)).toEqual(['approve', 'reject'])
    expect(line(w).text()).toBe('소유 충돌 Flow라 바꿀 수 없습니다.')
  })

  it('an approved document shows nothing (no empty place either)', async () => {
    docs.get(V01_DOC)!.status = 'approved'
    const w = await mountBar(v01Target({ docReviewStatus: 'approved' }))
    expect(w.find('[data-test="inspector-actions"]').exists()).toBe(false)
  })
})

// --- Events ----------------------------------------------------------------------------------------

describe('header actions — server events read again (D#1 §7.4)', () => {
  it('listens to the document and AI activity events only', async () => {
    await mountBar(v01Target())
    const [connection] = stream.connections()
    for (const event of [...ACTIONS_DOCUMENT_EVENTS, ...ACTIONS_ACTIVITY_EVENTS]) expect(connection.listened(event), event).toBeGreaterThan(0)
    expect(connection.listened('qna_q_registered')).toBe(0)
    expect(connection.listened('test_run_started')).toBe(0)
  })

  it('doc_review_status_changed of the document reads it again; another document does not', async () => {
    vi.useFakeTimers()
    const w = await mountBar(v01Target())
    stream.open()
    stream.emit('doc_review_status_changed', { docId: 'flowgate.v02.0048.0013-TR' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(documentRead).toHaveBeenCalledTimes(1)
    docs.get(V01_DOC)!.status = 'approved'
    stream.emit('doc_review_status_changed', { docId: V01_DOC, payload: { prev_status: 'pending_review', next_status: 'approved' } })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(shown(w)).toEqual([])
  })

  it('ai_invoke_started / finished of any document of the Flow read the activity again', async () => {
    vi.useFakeTimers()
    const w = await mountBar(v01Target())
    stream.open()
    running = true
    stream.emit('ai_invoke_started', { groupId: V01_FLOW, docId: 'flowgate.v02.0048.0013-TR' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(disabled(w, 'approve')).toBe(true)
    running = false
    stream.emit('ai_invoke_finished', { groupId: V01_FLOW, docId: 'flowgate.v02.0048.0013-TR' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(activityRead).toHaveBeenCalledTimes(3)
    expect(disabled(w, 'approve')).toBe(false)
    // Another Flow's run changes nothing here.
    stream.emit('ai_invoke_started', { groupId: 'flowgate.v02.0049', docId: 'flowgate.v02.0049.0007-TS' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(activityRead).toHaveBeenCalledTimes(3)
  })
})

// --- In the Frame ----------------------------------------------------------------------------------

describe('header actions in the Frame (real registry)', () => {
  async function mountFrame(selection: FlowGraphSelection) {
    wrapper = mount(DesignerInspector, { props: { selection }, global: { plugins: [i18n, pinia] } })
    await flushPromises()
    return wrapper
  }

  const withNode = (fields: Partial<FlowGraphNode>, docFields: Record<string, unknown> = {}): FlowGraphSelection => {
    const node: FlowGraphNode = { ...V01_TASK_NODE, ...fields, document: { ...V01_TASK_NODE.document!, ...docFields } as FlowGraphNode['document'] }
    return selectionOf({ ...V01_GRAPH, nodes: [node] }, node.id)
  }

  it('a v0.1 document under review: the bar sits in the header actions', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    const actions = w.get('[data-test="designer-inspector-actions"]')
    expect(actions.find('[data-test="inspector-actions"]').exists()).toBe(true)
    expect(actions.findAll('button').map((b) => b.text())).toEqual(['승인', '반려', '▶ 실행'])
  })

  it.each([
    ['v01 slot without a document', SELECTIONS.v01NoDocument],
    ['graph gate', SELECTIONS.graphGate],
  ])('%s: no header actions (D#1 §4.4)', async (_label, selection) => {
    const w = await mountFrame(selection)
    expect(w.find('[data-test="inspector-actions"]').exists()).toBe(false)
    expect(documentRead).not.toHaveBeenCalled()
  })

  it.each([
    ['AC (check card: T#12)', 'AC'],
    ['M (other)', 'M'],
  ])('%s: not registered', async (_label, typeCode) => {
    docs.set(V01_DOC, { status: 'pending_review', typeCode, disposed: false })
    const w = await mountFrame(withNode({ docType: typeCode }, { typeCode }))
    expect(w.find('[data-test="inspector-actions"]').exists()).toBe(false)
    expect(documentRead).not.toHaveBeenCalled()
  })

  it.each(['R', 'B', 'N', 'NR', 'T', 'TR', 'T2', 'TR2', 'DS', 'D', 'P', 'L', 'DB', 'TS', 'TSR', 'V', 'WP'])('%s under review: approve and reject', async (typeCode) => {
    docs.set(V01_DOC, { status: 'pending_review', typeCode, disposed: false })
    const w = await mountFrame(withNode({ docType: typeCode }, { typeCode }))
    const labels = w.findAll('[data-test="inspector-actions"] button').map((b) => b.text())
    expect(labels.slice(0, 2)).toEqual(['승인', '반려'])
    expect(labels.includes('▶ 실행')).toBe(['N', 'NR', 'T', 'TR', 'T2', 'TR2', 'DS', 'D', 'P', 'L', 'DB'].includes(typeCode))
  })

  it('a graph document Node under review: decision only, no Graph run / retry / gate button', async () => {
    const graph = SELECTIONS.graph
    const node: FlowGraphNode = { ...graph.node, document: { ...graph.node.document!, docReviewStatus: 'pending_review' } }
    docs.set(node.document!.docId, { status: 'pending_review', typeCode: 'TS', disposed: false })
    const w = await mountFrame({ ...graph, node })
    expect(w.findAll('[data-test="inspector-actions"] button').map((b) => b.text())).toEqual(['승인', '반려'])
  })

  it.each([
    ['v01 under review', SELECTIONS.v01, 'pending_review'],
    ['v01 rejected', SELECTIONS.v01, 'rejected'],
    ['graph', SELECTIONS.graph, 'pending_review'],
    ['unassigned', SELECTIONS.unassigned, 'pending_review'],
    ['conflict', SELECTIONS.conflict, 'rejected'],
  ])('%s: no undecided button (Graph run · re-run · retry · gate · expansion · delegation)', async (_label, selection, status) => {
    docs.set(selection.node.document!.docId, { status, typeCode: selection.node.document!.typeCode!, disposed: false })
    const w = await mountFrame(selection)
    const header = w.get('[data-test="designer-inspector-header"]').text()
    for (const word of ['재실행', '재시도', 'gate', '게이트', '전개', '위임', 'Retry', 'Re-run']) expect(header).not.toContain(word)
  })
})
