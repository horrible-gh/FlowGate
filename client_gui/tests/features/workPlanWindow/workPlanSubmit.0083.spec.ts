/**
 * flowgate.v02.0083 T#3 (0020-T D-1 · D-2 · D-3) — the work plan window of a WP card a person writes while its Flow runs.
 *
 * - the model: which plan is its run card's own (workPlanCardTarget: open with no result and the plan on its line, or in
 *   review with the plan as its result), its content lock (only the ownership conflict and the card's own AI — not the
 *   revision view, the run lock or another card's AI), [제출] (workPlanSubmitState) and why a [제출] was refused;
 * - the real Frame (DesignerInspector, real registry) and the real windows host: the card's 「작업계획」 line opens the plan
 *   in a revision view of a running Flow — the person edits, saves and [제출]s it (the card line write of the plan line, no
 *   body; then the Designer is asked to read the Flow again); the card's own running execution locks it, the Flow's AI run
 *   on another document does not; a rejected plan's [제출] is mark_revised; an ownership conflict locks it; a plan whose
 *   card is not waiting on it stays read only; 「지시 생략」 stays locked in the revision view (a structure action).
 *
 * `@shared/api` is the boundary (the plan's GET / PUT, the Flow's AI run read, the [제출] POSTs).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode, FlowGraphNodeRun, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import type { InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import { forgetWorkPlanOpeners } from '@/features/designer/inspector/openDocument'
import { closeAllDocumentWindows } from '@/features/documentWindow'
import { closeAllWorkPlanEditWindows, WorkPlanEditWindows, workPlanEditWindowList } from '@/features/workPlanWindow'
import {
  workPlanCardAiRunning,
  workPlanCardTarget,
  workPlanLockReason,
  workPlanSubmitFailure,
  workPlanSubmitState,
  type WorkPlanRunCard,
  type WorkPlanSubmitInput,
} from '@/features/workPlanWindow/workPlanEditModel'
import { prepareInspectorI18n, selectionOf } from '../designer/inspector/support/inspectorSupport'
import { DOC, FLOW, readAnswer } from './workPlanFixtures'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), postFormRequest: vi.fn() }))
vi.mock('@shared/api', async (original) => ({ ...(await original<typeof import('@shared/api')>()), ...api }))

const i18n = prepareInspectorI18n()

const WP = `/api/v1/documents/${encodeURIComponent(DOC)}/work-plan`
const SUBMIT = `/api/v1/flows/${encodeURIComponent(FLOW)}/nodes/gn_wp/slots/plan/document`
const REVISED = `/api/v1/documents/${encodeURIComponent(DOC)}/review_transitions/mark_revised`
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
const reject = (status: number, data: Record<string, unknown>) => ({ isAxiosError: true, response: { status, data } })

let answer: Record<string, any>
let activity: Record<string, unknown>
let intents: InspectorHostIntent[]
let frame: VueWrapper<any> | null = null
let host: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllWorkPlanEditWindows()
  closeAllDocumentWindows()
  forgetWorkPlanOpeners()
  for (const mock of Object.values(api)) mock.mockReset()
  answer = readAnswer({ status: 'draft', doc_review_status: null })
  activity = { active: false }
  intents = []
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === WP) return { data: copy(answer) }
    if (path === '/api/v1/ai-invoke/active') return { data: copy(activity) }
    return offline()
  })
  api.putRequest.mockImplementation(async (path: string, payload: any) => {
    if (path !== WP) return offline()
    const body = copy(payload.body)
    answer = { ...answer, body, revision_no: payload.base_revision_no + 1 }
    return { data: { ok: true, ...copy(answer) } }
  })
  api.postRequest.mockImplementation(async (path: string) => {
    if (path === SUBMIT) {
      answer = { ...answer, status: 'open', doc_review_status: 'pending_review' }
      return { data: { flow_id: FLOW, node_key: 'gn_wp', slot: 'plan', doc_type: 'WP', doc_id: DOC, bound_as_result: true, line: null } }
    }
    if (path === REVISED) {
      answer = { ...answer, doc_review_status: 'pending_review' }
      return { data: { document: { doc_id: DOC, type_code: 'WP', doc_review_status: 'pending_review' } } }
    }
    return offline()
  })
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
  document.body.innerHTML = ''
})

// --- The model ----------------------------------------------------------------------------------------------

const own = (fields: Partial<WorkPlanRunCard> = {}): WorkPlanRunCard => ({
  flowId: FLOW,
  nodeKey: 'gn_wp',
  status: 'open',
  resultDocId: null,
  runningExecutionCount: 0,
  lineDocId: DOC,
  conflict: false,
  ...fields,
})

describe('the run card’s own plan (flowgate.v02.0083 T#3, model)', () => {
  it('workPlanCardTarget: open with no result and the plan on its line → write; in review with the plan as its result → revise', () => {
    expect(workPlanCardTarget(DOC, own())).toBe('write')
    expect(workPlanCardTarget(DOC, own({ resultDocId: `${FLOW}.0009-WP` }))).toBeNull()
    expect(workPlanCardTarget(DOC, own({ lineDocId: `${FLOW}.0009-WP` }))).toBeNull()
    expect(workPlanCardTarget(DOC, own({ status: 'review', resultDocId: DOC }))).toBe('revise')
    expect(workPlanCardTarget(DOC, own({ status: 'review', resultDocId: `${FLOW}.0009-WP` }))).toBeNull()
    for (const status of ['pending', 'done', null]) expect(workPlanCardTarget(DOC, own({ status })), String(status)).toBeNull()
    expect(workPlanCardTarget(DOC, null)).toBeNull()
  })

  it('workPlanLockReason: the card’s plan ignores the revision view, the run lock and the Flow’s AI run; the conflict and its own AI lock it', () => {
    const base = { external: null, aiRunning: false, uploadGuarded: false, editable: true, editLockedReason: null } as const
    const free = { conflict: false, aiRunning: false }
    for (const external of ['read_only', 'run_locked'] as const) {
      expect(workPlanLockReason({ ...base, external, card: free }), external).toBeNull()
      expect(workPlanLockReason({ ...base, external }), external).toBe(external)       // any other plan: as before
    }
    expect(workPlanLockReason({ ...base, aiRunning: true, card: free })).toBeNull()
    expect(workPlanLockReason({ ...base, aiRunning: true })).toBe('ai_running')
    expect(workPlanLockReason({ ...base, external: 'read_only', card: { conflict: true, aiRunning: false } })).toBe('read_only')
    expect(workPlanLockReason({ ...base, external: 'run_locked', card: { conflict: false, aiRunning: true } })).toBe('ai_running')
    // The server's verdict, an upload and a refused save still lock it.
    expect(workPlanLockReason({ ...base, card: free, uploadGuarded: true })).toBe('upload')
    expect(workPlanLockReason({ ...base, card: free, editable: false, editLockedReason: 'final_approved' })).toBe('final_approved')
    expect(workPlanLockReason({ ...base, card: free, refused: 'disposed' })).toBe('disposed')
    expect(workPlanLockReason({ ...base, card: null, external: 'read_only' })).toBe('read_only')
  })

  it('workPlanCardAiRunning: its running executions or a document AI run on the plan', () => {
    expect(workPlanCardAiRunning(own(), false)).toBe(false)
    expect(workPlanCardAiRunning(own({ runningExecutionCount: 1 }), false)).toBe(true)
    expect(workPlanCardAiRunning(own(), true)).toBe(true)
  })

  it('workPlanSubmitState: write on the open card, mark_revised on a rejected plan; never with unsaved edits or a lock', () => {
    const input: WorkPlanSubmitInput = { target: 'write', docReviewStatus: null, lockReason: null, inputsLocked: false, dirty: false, busy: false }
    expect(workPlanSubmitState(input)).toEqual({ visible: true, enabled: true, action: 'write', reason: null })
    expect(workPlanSubmitState({ ...input, dirty: true })).toEqual({ visible: true, enabled: false, action: 'write', reason: 'submit.reason.dirty' })
    expect(workPlanSubmitState({ ...input, lockReason: 'ai_running', inputsLocked: true }).reason).toBe('lock.ai_running')
    expect(workPlanSubmitState({ ...input, busy: true, inputsLocked: true }).reason).toBe('submit.reason.busy')
    expect(workPlanSubmitState({ ...input, target: 'revise', docReviewStatus: 'rejected' })).toEqual({
      visible: true, enabled: true, action: 'mark_revised', reason: null,
    })
    expect(workPlanSubmitState({ ...input, target: 'revise', docReviewStatus: 'pending_review' }).visible).toBe(false)
    expect(workPlanSubmitState({ ...input, target: null }).visible).toBe(false)
  })

  it('workPlanSubmitFailure: empty, its own AI, already submitted, another AI (423), the card moved on, forbidden, network', () => {
    expect(workPlanSubmitFailure({ status: 422, code: 'invalid_request', reason: 'empty_body' })).toBe('empty')
    expect(workPlanSubmitFailure({ status: 409, code: 'node_ai_working' })).toBe('ai_working')
    expect(workPlanSubmitFailure({ status: 409, code: 'node_token_unavailable' })).toBe('taken')
    expect(workPlanSubmitFailure({ status: 409, code: 'slot_has_document' })).toBe('taken')
    expect(workPlanSubmitFailure({ status: 423, code: 'GROUP_AI_RUN_LOCKED' })).toBe('ai_running')
    expect(workPlanSubmitFailure({ status: 409, code: null })).toBe('conflict')
    expect(workPlanSubmitFailure({ status: 403, code: 'flow_access_denied' })).toBe('forbidden')
    expect(workPlanSubmitFailure({ status: null, code: null, network: true })).toBe('network')
    expect(workPlanSubmitFailure({ status: 500, code: null })).toBe('other')
  })
})

// --- The window through the Inspector's 「작업계획」 line ----------------------------------------------------

const line = (slot: string, docType: string, docId: string | null): FlowGraphCardDocument => ({
  slot,
  docType,
  author: slot === 'plan' ? 'human' : 'run',
  docId,
  title: slot === 'plan' ? '작업계획 — 설계 1장 · 작업 2세트' : '전개',
  status: 'draft',
  docReviewStatus: null,
})

function wpCard(run: Partial<FlowGraphNodeRun> | null): FlowGraphNode {
  return {
    id: 'gn_wp',
    kind: 'plan',
    title: '작업계획 카드',
    state: 'in_progress',
    docType: 'WP',
    x: 0,
    y: 0,
    structureEditable: false,
    definition: {
      nodeType: 'document', docType: 'WP', paletteItemId: null, label: '작업계획 카드',
      assignee: { kind: 'human', userId: 'u_lee', providerId: null }, rootBound: false,
    },
    document: null,
    cardDocuments: [line('plan', 'WP', DOC), line('deploy', 'M', null)],
    nodeRun: run ? ({ status: 'open', openedAt: null, finishedAt: null, resultDocId: null, ...run } as FlowGraphNodeRun) : null,
  } as FlowGraphNode
}

function selection(node: FlowGraphNode, ownership = 'graph'): FlowGraphSelection {
  const graph = {
    flowId: FLOW,
    nodes: [node],
    edges: [],
    ownership,
    viewMode: 'revision',
    draft: { version: 1, editable: false, confirmable: false, blockedReason: null, baseRevisionNo: 1 },
  } as unknown as FlowGraph
  return { ...selectionOf(graph, node.id, 'flowgate'), editLock: 'run' }
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

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const row = (id: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-step"][data-row="${id}"]`)!
const disabled = (node: HTMLElement | null) => (node as HTMLButtonElement | HTMLInputElement).disabled
const note = () => q('wp-edit-step-note', row('c_t2|main'))
const posts = () => api.postRequest.mock.calls.map(([path, body]) => [path, body])

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

async function openPlan() {
  const plan = q('inspector-work-plan')!.querySelector<HTMLElement>('[data-test="inspector-slot"][data-slot="plan"]')!
  await click(plan.querySelector<HTMLElement>('[data-test="inspector-slot-name"]'))
  expect(workPlanEditWindowList().map((win) => win.docId)).toEqual([DOC])
}

describe('a person’s WP while its Flow runs (flowgate.v02.0083 T#3, window)', () => {
  it('revision view of a running Flow, the card open: edit → [저장] → [제출] (the card line write, no body) → the Flow is read again', async () => {
    await mountAll(selection(wpCard({ status: 'open' })))
    await openPlan()
    expect(q('wp-edit-lock')).toBeNull()                                  // not 'read_only' / 'run_locked' any more
    expect(disabled(note())).toBe(false)
    expect(q('wp-edit-submit')!.dataset.action).toBe('write')
    expect(disabled(q('wp-edit-submit'))).toBe(false)
    await type(note(), '사람이 쓴 작업계획')
    expect(disabled(q('wp-edit-submit'))).toBe(true)                      // unsaved edits: save first
    expect(q('wp-edit-submit')!.dataset.reason).toBe('submit.reason.dirty')
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    expect(disabled(q('wp-edit-submit'))).toBe(false)
    intents.length = 0
    await click(q('wp-edit-submit'))
    expect(posts()).toEqual([[SUBMIT, { content: null }]])
    expect(q('wp-edit-notice')!.dataset.key).toBe('submitted')
    expect(intents).toContainEqual({ kind: 'refresh-flow', flowId: FLOW })
  })

  it('the card’s own running execution (its review) locks it; the Flow’s AI run on another document does not', async () => {
    activity = { active: true, run_id: 'air_other', status: 'running', doc_ref: `${FLOW}.0009-T` }
    const w = await mountAll(selection(wpCard({ status: 'open' })))
    await openPlan()
    expect(q('wp-edit-lock')).toBeNull()
    expect(disabled(note())).toBe(false)
    await w.setProps({ selection: selection(wpCard({ status: 'open', runningExecutionCount: 1 })) })
    await flushPromises()
    expect(q('wp-edit-lock')!.dataset.reason).toBe('ai_running')
    expect(disabled(note())).toBe(true)
    expect(disabled(q('wp-edit-submit'))).toBe(true)
    await click(q('wp-edit-submit'))
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it('a plan whose card waits on nothing (no node run, or done) stays read only in the revision view, with no [제출]', async () => {
    const w = await mountAll(selection(wpCard(null)))
    await openPlan()
    expect(q('wp-edit-lock')!.dataset.reason).toBe('read_only')
    expect(q('wp-edit-submit')).toBeNull()
    await w.setProps({ selection: selection(wpCard({ status: 'done', resultDocId: DOC })) })
    await flushPromises()
    expect(q('wp-edit-lock')!.dataset.reason).toBe('read_only')
    expect(q('wp-edit-submit')).toBeNull()
  })

  it('an ownership conflict locks the card’s plan too', async () => {
    await mountAll(selection(wpCard({ status: 'open' }), 'conflict'))
    await openPlan()
    expect(q('wp-edit-lock')!.dataset.reason).toBe('read_only')
    expect(disabled(q('wp-edit-submit'))).toBe(true)
  })

  it('a rejected plan of a card in review: edit → [저장] → [제출] is mark_revised', async () => {
    answer = readAnswer({ status: 'open', doc_review_status: 'rejected' })
    await mountAll(selection(wpCard({ status: 'review', resultDocId: DOC })))
    await openPlan()
    expect(q('wp-edit-lock')).toBeNull()
    expect(q('wp-edit-submit')!.dataset.action).toBe('mark_revised')
    await type(note(), '반려 뒤 고침')
    await click(q('wp-edit-save'))
    await click(q('wp-edit-submit'))
    expect(posts()).toEqual([[REVISED, {}]])
    expect(q('wp-edit-notice')!.dataset.key).toBe('submitted')
  })

  it('a refused [제출] says why (작업 0세트: 422 empty_body) and the plan stays editable', async () => {
    api.postRequest.mockImplementation(async () => {
      throw reject(422, { ok: false, error: { code: 'invalid_request', message: 'x', params: { errors: [{ field: 'content', reason: 'empty_body' }] } } })
    })
    await mountAll(selection(wpCard({ status: 'open' })))
    await openPlan()
    await click(q('wp-edit-submit'))
    const shown = q('wp-edit-notice')!
    expect(shown.dataset.key).toBe('submit_failed')
    expect(shown.textContent).toContain('작업이 0세트인 작업계획은 제출할 수 없습니다.')
    expect(disabled(note())).toBe(false)
  })

  it('「지시 생략」 stays a structure action: off in the revision view while the content is editable', async () => {
    await mountAll(selection(wpCard({ status: 'open' })))
    await openPlan()
    expect(disabled(note())).toBe(false)
    const omit = q('wp-edit-step-omit', row('c_t2|main'))
    expect(omit).not.toBeNull()
    expect(disabled(omit)).toBe(true)
  })
})
