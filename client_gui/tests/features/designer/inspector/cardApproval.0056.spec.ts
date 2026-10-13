/**
 * flowgate.v02.0056 F2-1 — the card approval bar at the bottom of the documents tab (O1 0005-TR §4-1 / §4-2,
 * O2 0007-TR §4-2 / §4-3), with `@/features/document`'s run read and card approval mocked:
 *
 * - the card state (the Canvas card's rule): 지시서 작성 중 · 결과 작성 중 · 작업 중 → no [승인]; 승인 대기 → [승인];
 *   승인됨 → none; a rejected R/B root → none;
 * - [승인] reads the Flow's run and approves the card with the result the screen shows, then asks the Designer
 *   to read the Flow again (host `refresh-flow`: the card, its lines and the run band follow the server);
 * - every refusal by its code: result_changed → read again and told; group_ai_running · card_approval_busy ·
 *   ai_running → the button stays, a toast; already_approved · node_not_in_review · no_result · node_not_started
 *   → read again; document_approval_refused → the document (it opens), why, and the documents approved before
 *   it; 403 approval_forbidden → the permission; the server's message shown as sent;
 * - the real registry: the bar sits in a Graph Flow's documents tab (not in the header — no 승인 / 반려 there),
 *   never on a v0.1 Flow; the instruction line says who writes it (지시 AI 또는 사람).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const runRead = vi.hoisted(() => vi.fn())
const approveCall = vi.hoisted(() => vi.fn())
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    runCurrentRead: (...args: unknown[]) => runRead(...args),
    runCardApprove: (...args: unknown[]) => approveCall(...args),
  }
})

// Every other read of the Frame's features answers nothing.
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline) }
})

import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode, FlowGraphNodeRun } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import CardApprovalBar from '@/features/designer/inspector/features/cardApproval/CardApprovalBar.vue'
import { cardApprovalBar, cardApprovalDisabledReason, cardApprovalToasted } from '@/features/designer/inspector/features/cardApproval/cardApprovalModel'
import registration from '@/features/designer/inspector/features/cardApproval/register'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import { inspectorCardState, toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import { slotAuthorKey } from '@/features/designer/inspector/parts/slotStateView'
import { SELECTIONS, prepareInspectorI18n } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0061'
const T = `${FLOW}.0002-T`
const TR = `${FLOW}.0003-TR`
const TR_NEW = `${FLOW}.0004-TR`

const nodeRun = (status: string, fields: Partial<FlowGraphNodeRun> = {}): FlowGraphNodeRun => ({ status, openedAt: null, finishedAt: null, resultDocId: null, ...fields })
const line = (slot: string, docType: string, docId: string, author: string, docReviewStatus: string | null): FlowGraphCardDocument => ({
  slot,
  docType,
  author,
  docId,
  title: `작업 · ${slot}`,
  status: 'open',
  docReviewStatus,
})

function taskCard(run: FlowGraphNodeRun | null, fields: Partial<FlowGraphNode> = {}): FlowGraphNode {
  return {
    id: 'gn_task',
    kind: 'task',
    title: '작업',
    state: 'in_progress',
    docType: 'T',
    x: 0,
    y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: '작업', assignee: null },
    document: null,
    // The result may already be approved while the card waits (only its instruction is left — O1 4-2).
    cardDocuments: [line('instruction', 'T', T, 'instruction_ai_or_human', null), line('report', 'TR', TR, 'run', 'approved')],
    nodeRun: run,
    ...fields,
  }
}

const graphOf = (node: FlowGraphNode, ownership = 'graph'): FlowGraph => ({ flowId: FLOW, nodes: [node], edges: [], ownership, viewMode: 'revision' })
const targetFor = (node: FlowGraphNode, ownership = 'graph'): InspectorTarget => toInspectorTarget({ projectId: 'flowgate', graph: graphOf(node, ownership), node })!

const AWAITING = nodeRun('review', { awaitingApproval: true, stage: 'result', resultDocId: TR })
const RUN = { runId: 'run_r1', status: 'stopped', nodes: [{ nodeKey: 'gn_task', status: 'review', awaitingApproval: true, resultDocId: TR }] }

function refusal(code: string, status: number, params: Record<string, unknown> = {}, message = code) {
  return Object.assign(new Error('Request failed'), { isAxiosError: true, response: { status, data: { ok: false, error: { code, message, params } } } })
}

let wrapper: VueWrapper<any> | null = null
let intents: InspectorHostIntent[] = []

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  intents = []
  runRead.mockReset()
  approveCall.mockReset()
  runRead.mockResolvedValue(RUN)
  approveCall.mockResolvedValue({ run: { ...RUN, status: 'stopped' }, approval: { nodeKey: 'gn_task', targetDocIds: [T, TR], approvedDocIds: [T] } })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

async function mountBar(target: InspectorTarget) {
  wrapper = mount(CardApprovalBar, {
    props: { target },
    global: { plugins: [i18n], provide: { [INSPECTOR_HOST as symbol]: { request: (intent: InspectorHostIntent) => intents.push(intent) } } },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

const bar = (w: VueWrapper<any>) => w.find('[data-test="inspector-card-approval"]')
const approveButton = (w: VueWrapper<any>) => w.find('[data-test="card-approval-approve"]')
const failure = (w: VueWrapper<any>) => w.find('[data-test="card-approval-failure"]')
const toasts = () => [...document.body.querySelectorAll('.ui-toast-host')].map((host) => host.textContent?.trim() ?? '')
const refreshes = () => intents.filter((intent) => intent.kind === 'refresh-flow')

async function press(w: VueWrapper<any>) {
  await approveButton(w).trigger('click')
  await flushPromises()
}

// --- The model --------------------------------------------------------------------------------------

describe('cardApprovalModel (flowgate.v02.0056 F2-1)', () => {
  const awaiting = inspectorCardState(targetFor(taskCard(AWAITING)))

  it('shown with a card state; [승인] only while it waits; usable unless an ownership conflict or an approval in flight', () => {
    expect(cardApprovalBar({ state: null, readOnly: null, busy: false })).toEqual({ shown: false, approveVisible: false, enabled: false, reason: null })
    expect(cardApprovalBar({ state: inspectorCardState(targetFor(taskCard(nodeRun('done')))), readOnly: null, busy: false })).toEqual({ shown: true, approveVisible: false, enabled: false, reason: null })
    expect(cardApprovalBar({ state: awaiting, readOnly: null, busy: false })).toEqual({ shown: true, approveVisible: true, enabled: true, reason: null })
    expect(cardApprovalBar({ state: awaiting, readOnly: 'conflict', busy: false })).toMatchObject({ enabled: false, reason: 'reason.read_only_conflict' })
    expect(cardApprovalBar({ state: awaiting, readOnly: null, busy: true })).toMatchObject({ enabled: false, reason: 'reason.busy' })
    // The run view (a confirmed revision) is where a card is approved: not read only for the approval.
    expect(cardApprovalDisabledReason({ state: awaiting, readOnly: 'revision', busy: false })).toBeNull()
  })

  it('no card state on a gate, a card not saved yet, or a Node without a run', () => {
    expect(inspectorCardState(targetFor(taskCard(AWAITING, { definition: { nodeType: 'gate', docType: null, paletteItemId: null, label: '확인', assignee: null } })))).toBeNull()
    expect(inspectorCardState(targetFor(taskCard(AWAITING, { id: 'tmp_1' })))).toBeNull()
    expect(inspectorCardState(targetFor(taskCard(null)))).toBeNull()
  })

  it('only a busy refusal is a toast', () => {
    const of = (kind: string) => ({ view: { kind, labelKey: '', reread: false, keepButton: kind === 'busy' }, code: null, message: '', docId: null, reason: null, approvedDocIds: [] }) as never
    expect(cardApprovalToasted(of('busy'))).toBe(true)
    for (const kind of ['changed', 'stale', 'refused', 'forbidden', 'inactive', 'network', 'other']) expect(cardApprovalToasted(of(kind))).toBe(false)
    expect(cardApprovalToasted(null)).toBe(false)
  })

  it('applies to Graph Flows only, in the documents tab (not the header)', () => {
    expect(registration.ownerships).toEqual(['graph', 'conflict'])
    expect(registration.tab?.key).toBe('documents')
    expect(registration.headerAction).toBeUndefined()
    expect(registration.cardKinds).not.toContain('check')
  })
})

// --- The bar ----------------------------------------------------------------------------------------

describe('the card approval bar — states', () => {
  it.each([
    ['open · instruction', nodeRun('open', { stage: 'instruction' }), '지시서 작성 중'],
    ['open · result', nodeRun('open', { stage: 'result' }), '결과 작성 중'],
    ['review · an AI works', nodeRun('review', { awaitingApproval: false, resultDocId: TR }), '작업 중'],
    ['done', nodeRun('done', { resultDocId: TR }), '승인됨'],
    ['rejected R/B root', nodeRun('rejected', { awaitingApproval: false }), '반려됨'],
  ])('%s: the state, no [승인]', async (_label, run, text) => {
    const w = await mountBar(targetFor(taskCard(run)))
    expect(bar(w).get('[data-test="card-approval-state"]').text()).toContain(text)
    expect(approveButton(w).exists()).toBe(false)
    expect(runRead).not.toHaveBeenCalled()
  })

  it('a card waiting for its approval: 승인 대기 and [승인] (its result already approved — the card state decides)', async () => {
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    expect(bar(w).attributes('data-phase')).toBe('awaiting')
    expect(bar(w).get('[data-test="card-approval-state"]').text()).toContain('승인 대기')
    expect(approveButton(w).text()).toBe('승인')
    expect(approveButton(w).attributes('disabled')).toBeUndefined()
    expect(approveButton(w).attributes('title')).toBe('이 카드의 지시서와 결과를 승인합니다')
  })

  it('nothing on a gate or a Node without a run', async () => {
    expect(bar(await mountBar(targetFor(taskCard(null)))).exists()).toBe(false)
    wrapper!.unmount()
    const gate = taskCard(AWAITING, { definition: { nodeType: 'gate', docType: null, paletteItemId: null, label: '확인', assignee: null } })
    expect(bar(await mountBar(targetFor(gate))).exists()).toBe(false)
  })

  it('an ownership conflict: [승인] shown off with its reason', async () => {
    const w = await mountBar(targetFor(taskCard(AWAITING), 'conflict'))
    expect(approveButton(w).attributes('disabled')).toBeDefined()
    expect(approveButton(w).attributes('title')).toBe('담당이 겹친 플로우라 승인할 수 없습니다')
    await press(w)
    expect(approveCall).not.toHaveBeenCalled()
  })

  it('follows the locale', async () => {
    i18n.global.locale.value = 'en'
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    expect(bar(w).get('[data-test="card-approval-state"]').text()).toContain('Awaiting approval')
    expect(approveButton(w).text()).toBe('Approve')
  })
})

describe('the card approval bar — [승인]', () => {
  it('reads the Flow’s run, approves the card with the shown result, and asks for the Flow to be read again', async () => {
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(runRead).toHaveBeenCalledWith(FLOW)
    expect(approveCall).toHaveBeenCalledWith('run_r1', 'gn_task', TR)
    expect(refreshes()).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
    expect(failure(w).exists()).toBe(false)
    // The Canvas read again shows the card done: the bar says so.
    await w.setProps({ target: targetFor(taskCard(nodeRun('done', { resultDocId: TR }))) })
    expect(w.get('[data-test="card-approval-done"]').text()).toBe('카드를 승인했습니다')
  })

  it('while it is answered the button is busy and a second press sends nothing', async () => {
    let answer!: (value: unknown) => void
    approveCall.mockImplementation(() => new Promise((resolve) => (answer = resolve)))
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(approveButton(w).attributes('disabled')).toBeDefined()
    await press(w)
    answer({ run: RUN, approval: { nodeKey: 'gn_task', targetDocIds: [TR], approvedDocIds: [TR] } })
    await flushPromises()
    expect(approveCall).toHaveBeenCalledTimes(1)
  })

  it('409 result_changed: the card is read again and the change is told', async () => {
    approveCall.mockRejectedValue(refusal('result_changed', 409, { run_id: 'run_r1', node_key: 'gn_task', result_doc_id: TR_NEW }, '결과가 바뀌었습니다 (서버)'))
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(failure(w).attributes('data-kind')).toBe('changed')
    expect(failure(w).text()).toContain('결과 문서가 바뀌었습니다. 카드를 다시 읽었으니 확인한 뒤 다시 승인하세요.')
    expect(w.get('[data-test="card-approval-message"]').text()).toBe('결과가 바뀌었습니다 (서버)')
    expect(refreshes()).toHaveLength(1)
    // The Canvas read again carries the new result; the next press sends it.
    await w.setProps({ target: targetFor(taskCard(nodeRun('review', { awaitingApproval: true, resultDocId: TR_NEW }))) })
    approveCall.mockResolvedValue({ run: RUN, approval: { nodeKey: 'gn_task', targetDocIds: [TR_NEW], approvedDocIds: [TR_NEW] } })
    await press(w)
    expect(approveCall).toHaveBeenLastCalledWith('run_r1', 'gn_task', TR_NEW)
  })

  it.each([
    ['group_ai_running', '이 그룹에서 다른 AI가 실행 중입니다. 끝난 뒤 다시 승인하세요.'],
    ['card_approval_busy', '다른 카드를 승인하는 중입니다. 잠시 뒤 다시 승인하세요.'],
    ['ai_running', 'AI가 아직 이 카드를 작업하고 있습니다. 끝난 뒤 다시 승인하세요.'],
  ])('409 %s: the button stays, a toast (the sentence and the server message); nothing read again', async (code, text) => {
    approveCall.mockRejectedValue(refusal(code, 409, { flow_id: FLOW }, '잠시 뒤에'))
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(approveButton(w).attributes('disabled')).toBeUndefined()
    expect(failure(w).exists()).toBe(false)
    expect(toasts().join(' ')).toContain(`${text} — 잠시 뒤에`)
    expect(refreshes()).toHaveLength(0)
  })

  it.each([
    ['already_approved', '이미 승인된 카드입니다.'],
    ['node_not_in_review', '승인할 수 있는 상태가 아닙니다.'],
    ['no_result', '아직 결과가 없습니다.'],
    ['node_not_started', '아직 시작하지 않은 카드입니다.'],
  ])('409 %s: the card is read again, the reason told', async (code, text) => {
    approveCall.mockRejectedValue(refusal(code, 409, { run_id: 'run_r1', node_key: 'gn_task', status: 'done' }))
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(failure(w).attributes('data-kind')).toBe('stale')
    expect(failure(w).text()).toContain(text)
    // A message that only repeats the code says nothing more.
    expect(w.find('[data-test="card-approval-message"]').exists()).toBe(false)
    expect(refreshes()).toHaveLength(1)
  })

  it('409 document_approval_refused: the refused document (it opens), why, the documents approved before it; read again', async () => {
    approveCall.mockRejectedValue(refusal('document_approval_refused', 409, { doc_id: TR, reason: 'tr2_validation_failed', approved_doc_ids: [T] }, 'TR2 검증 실패'))
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(failure(w).attributes('data-kind')).toBe('refused')
    expect(failure(w).text()).toContain('문서 승인이 거절되었습니다.')
    expect(w.get('[data-test="card-approval-message"]').text()).toBe('TR2 검증 실패')
    expect(w.get('[data-test="card-approval-refused-doc"]').attributes('data-doc')).toBe(TR)
    expect(w.get('[data-test="card-approval-refused-reason"]').text()).toBe('사유: tr2_validation_failed')
    expect(w.get('[data-test="card-approval-approved-before"]').text()).toBe('앞서 승인된 문서 1개')
    expect(w.find('[data-test="card-approval-refused-open"]').exists()).toBe(true)
    expect(refreshes()).toHaveLength(1)
  })

  it('403 approval_forbidden: the permission is told; nothing read again', async () => {
    approveCall.mockRejectedValue(refusal('approval_forbidden', 403, { required: 'document.approve' }))
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(failure(w).attributes('data-kind')).toBe('forbidden')
    expect(failure(w).text()).toContain('승인 권한이 없습니다.')
    expect(refreshes()).toHaveLength(0)
  })

  it('no running or stopped run: nothing approved, told and read again', async () => {
    runRead.mockResolvedValue(null)
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    expect(approveCall).not.toHaveBeenCalled()
    expect(failure(w).attributes('data-kind')).toBe('inactive')
    expect(refreshes()).toHaveLength(1)
  })

  it('a refusal answered after another card was selected is not shown there', async () => {
    let fail!: (error: unknown) => void
    approveCall.mockImplementation(() => new Promise((_resolve, reject) => (fail = reject)))
    const w = await mountBar(targetFor(taskCard(AWAITING)))
    await press(w)
    await w.setProps({ target: targetFor(taskCard(AWAITING, { id: 'gn_other' })) })
    fail(refusal('no_result', 409))
    await flushPromises()
    expect(failure(w).exists()).toBe(false)
  })
})

// --- In the Frame -------------------------------------------------------------------------------------

describe('the card approval bar in the Frame (real registry)', () => {
  async function mountFrame(node: FlowGraphNode, ownership = 'graph') {
    wrapper = mount(DesignerInspector, { props: { selection: { projectId: 'flowgate', graph: graphOf(node, ownership), node } }, global: { plugins: [i18n, createPinia()] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  it('a Graph card waiting for its approval: the bar is the last part of the documents tab; the header has no 승인 / 반려', async () => {
    const w = await mountFrame(taskCard(AWAITING))
    const sections = w.findAll('[data-test="designer-inspector-section"]').map((section) => section.attributes('data-key'))
    expect(sections.at(-1)).toBe('cardApproval')
    expect(w.find('[data-test="inspector-card-approval"] [data-test="card-approval-approve"]').exists()).toBe(true)
    const header = w.get('[data-test="designer-inspector-header"]')
    expect(header.find('[data-test="inspector-actions"]').exists()).toBe(false)
    expect(header.findAll('button').map((button) => button.text())).not.toContain('승인')
    expect(header.findAll('button').map((button) => button.text())).not.toContain('반려')
  })

  it('no line names who writes it (0017-CH: no "지시 AI 또는 사람"); each line keeps its own review state', async () => {
    const w = await mountFrame(taskCard(AWAITING))
    const instruction = w.findAll('[data-test="inspector-slot"]').find((slot) => slot.attributes('data-doc') === T)!
    expect(instruction.find('[data-test="inspector-slot-author"]').exists()).toBe(false)
    expect(instruction.text()).not.toContain('지시 AI 또는 사람')
    const report = w.findAll('[data-test="inspector-slot"]').find((slot) => slot.attributes('data-doc') === TR)!
    expect(report.find('[data-test="inspector-slot-author"]').exists()).toBe(false)
    // The result was approved already; the card still waits (its instruction is left).
    expect(report.get('[data-test="inspector-slot-state"]').text()).toBe('승인됨')
  })

  it('a v0.1 Flow’s Node: no approval bar (its documents tab stays as it was)', async () => {
    wrapper = mount(DesignerInspector, { props: { selection: SELECTIONS.v01 }, global: { plugins: [i18n, createPinia()] }, attachTo: document.body })
    await flushPromises()
    expect(wrapper.findAll('[data-test="designer-inspector-section"]').map((section) => section.attributes('data-key'))).toEqual(['taskCard'])
    expect(wrapper.find('[data-test="inspector-card-approval"]').exists()).toBe(false)
  })
})

// --- The author of a card line (O2 4-3) -------------------------------------------------------------

describe('the card line author (instruction_ai_or_human)', () => {
  it('no author is named (0017-CH)', () => {
    for (const author of ['instruction_ai_or_human', 'human', 'run', null, undefined, 'other']) expect(slotAuthorKey(author)).toBeNull()
  })

  it('no line draws an author', () => {
    for (const author of ['instruction_ai_or_human', 'human', 'run']) {
      const w = mount(InspectorSlotRow, { props: { name: '레포트', id: TR, author }, global: { plugins: [i18n] } })
      expect(w.find('[data-test="inspector-slot-author"]').exists()).toBe(false)
      w.unmount()
    }
  })
})
