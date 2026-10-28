/**
 * flowgate.v02.0056 F2-1 — the card approval on the Canvas and the run band (O1 0005-TR §4-1 / §4-2, O2 §4-2):
 *
 * - the Canvas card's state chip follows cardStateView (지시서 작성 중 · 결과 작성 중 · 작업 중 · 승인 대기 · 승인됨 …)
 *   and offers [승인] only on a card waiting for its approval — never on a gate, an ownership conflict or a
 *   source that cannot approve; [승인] is an `approve` event of the card (not a selection);
 * - the run control's approve: the run's id (read when unknown), the shown result, the answered run kept (its
 *   pending propagation), the Canvas read again, one approval per Flow, the run buttons untouched; a refusal is
 *   the Flow's run error of kind `approval` grouped by cardStateView, keeping what its params name (the refused
 *   document, why, the documents approved before it — the Shell's toast, canvasApprovalShell.0056);
 * - the Center session approves a card with the result its Canvas read shows, and a Canvas [승인] reaches it;
 * - the run band: "멈춤 · 다음 카드 N개 대기" on a stopped run with cards approved meanwhile (their keys in the
 *   tooltip) — and a card approved while the run is stopped stays 승인됨 on the Canvas, never 승인 대기.
 *
 * flowgate.v02.0105 T#2 — expectations revised for NR flowgate.v02.0101.0003 §3.9 B-6: the Canvas card offers [승인] only
 * when the Flow's confirmed run read says the user may approve (`viewer.can_approve`). The run here is answered for such a
 * user (`viewerCanApprove: true`); one the server does not allow shows the state and no [승인].
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { toVueFlowNodes, type FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { cardStateView } from '@/features/designer/center/graph/cardStateView'
import type { FlowGraph, FlowGraphNode, FlowGraphNodeRun } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { runPendingView } from '@/features/designer/top/designerTop'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'

registerAppMessages(i18n)

const P = 'p'
const F = 'p.ui.0056'
const TR = `${F}.0005-TR`

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
})

const nodeRun = (status: string, fields: Partial<FlowGraphNodeRun> = {}): FlowGraphNodeRun => ({ status, openedAt: null, finishedAt: null, resultDocId: null, ...fields })

function card(id: string, run: FlowGraphNodeRun | null, nodeType = 'document', fields: Partial<FlowGraphNode> = {}): FlowGraphNode {
  return {
    id,
    kind: nodeType === 'gate' ? 'approval' : 'task',
    title: `카드 ${id}`,
    state: run?.status === 'done' ? 'done' : 'in_progress',
    docType: nodeType === 'gate' ? null : 'T',
    x: 0,
    y: 0,
    definition: { nodeType, docType: nodeType === 'gate' ? null : 'T', paletteItemId: null, label: id, assignee: null },
    nodeRun: run,
    ...fields,
  }
}

const AWAITING = card('gn_wait', nodeRun('review', { awaitingApproval: true, stage: 'result', resultDocId: TR }))
const WRITING = card('gn_inst', nodeRun('open', { stage: 'instruction' }))
const RESULT = card('gn_res', nodeRun('open', { stage: 'result' }))
const WORKING = card('gn_loop', nodeRun('review', { awaitingApproval: false, resultDocId: TR }))
const DONE = card('gn_done', nodeRun('done', { resultDocId: `${F}.0003-TR` }))
const ROOT = card('gn_root', nodeRun('rejected', { awaitingApproval: false }), 'document', { kind: 'requirement', docType: 'R' })
const GATE = card('gn_gate', nodeRun('review', { awaitingApproval: true }), 'gate')
const PLAIN = card('gn_plain', null)

const graphOf = (nodes: FlowGraphNode[], ownership = 'graph'): FlowGraph => ({ flowId: F, nodes, edges: [], ownership, viewMode: 'revision' })

const dataOf = (graph: FlowGraph, options = { canApprove: true, approvingNodeId: null as string | null }) =>
  Object.fromEntries(toVueFlowNodes(graph, null, new Map(), options).map((node) => [node.id, node.data])) as Record<string, FlowGateNodeData>

// --- The Canvas card ---------------------------------------------------------------------------------

describe('the Canvas card state and [승인] (vueFlowMapping)', () => {
  const graph = graphOf([AWAITING, WRITING, RESULT, WORKING, DONE, ROOT, GATE, PLAIN])

  it('each Graph card with a run carries its card state; [승인] only on the one waiting for its approval', () => {
    const data = dataOf(graph)
    const rows = Object.entries(data).map(([id, item]) => [id, item.cardState?.key ?? null, item.approvable === true])
    expect(rows).toEqual([
      ['gn_wait', 'awaiting_approval', true],
      ['gn_inst', 'writing_instruction', false],
      ['gn_res', 'writing_result', false],
      ['gn_loop', 'working', false],
      ['gn_done', 'approved', false],
      ['gn_root', 'rejected', false],
      // A gate decides with ✓ 확인 · ✕ 반려 (its own card): no card state, no [승인].
      ['gn_gate', null, false],
      ['gn_plain', null, false],
    ])
  })

  it('no [승인] when the Canvas may not approve, or on an ownership conflict; the card being approved is marked', () => {
    expect(dataOf(graph, { canApprove: false, approvingNodeId: null }).gn_wait.approvable).toBeUndefined()
    expect(dataOf(graphOf([AWAITING], 'conflict')).gn_wait.approvable).toBeUndefined()
    expect(dataOf(graph, { canApprove: true, approvingNodeId: 'gn_wait' }).gn_wait.approving).toBe(true)
    // The state chip stays either way.
    expect(dataOf(graphOf([AWAITING], 'conflict')).gn_wait.cardState?.key).toBe('awaiting_approval')
  })

  it('a card approved while the run is stopped is 승인됨, not 승인 대기 (the stop is the run band’s)', () => {
    // O1 1-2: in a stopped run an approval makes the node done at once; only the propagation waits.
    const stoppedRunDone = card('gn_a', nodeRun('done', { awaitingApproval: false, stage: 'result', resultDocId: TR }))
    const data = dataOf(graphOf([stoppedRunDone]))
    expect(data.gn_a.cardState?.key).toBe('approved')
    expect(data.gn_a.approvable).toBeUndefined()
  })
})

describe('FlowNodeCard — the state chip and [승인]', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    i18n.global.locale.value = 'ko'
  })

  const mountCard = (data: FlowGateNodeData) => {
    wrapper = mount(FlowNodeCard, { props: { id: 'gn_wait', data }, global: { plugins: [i18n], stubs: { Handle: true } } })
    return wrapper
  }
  const base: FlowGateNodeData = { kind: 'task', title: '카드', state: 'in_progress', selected: false }

  it('a card waiting for its approval: the 승인 대기 chip and [승인], which emits approve', async () => {
    i18n.global.locale.value = 'ko'
    const w = mountCard({ ...base, cardState: cardStateView({ status: 'review', awaitingApproval: true })!, approvable: true })
    const chip = w.get('[data-test="designer-canvas-node-card-state"]')
    expect(chip.text()).toBe('승인 대기')
    expect(chip.attributes('data-phase')).toBe('awaiting')
    expect(w.find('[data-test="designer-canvas-node-state"]').exists()).toBe(false)
    const approve = w.get('[data-test="designer-canvas-node-approve"]')
    expect(approve.text()).toBe('승인')
    expect(approve.classes()).toEqual(expect.arrayContaining(['nodrag', 'nopan']))
    await approve.trigger('click')
    expect(w.emitted('approve')).toEqual([['gn_wait']])
  })

  it('while its approval is answered the button is off', () => {
    const w = mountCard({ ...base, cardState: cardStateView({ status: 'review', awaitingApproval: true })!, approvable: true, approving: true })
    expect(w.get('[data-test="designer-canvas-node-approve"]').attributes('disabled')).toBeDefined()
  })

  it.each([
    [{ status: 'open', stage: 'instruction' }, '지시서 작성 중'],
    [{ status: 'open', stage: 'result' }, '결과 작성 중'],
    [{ status: 'review', awaitingApproval: false }, '작업 중'],
    [{ status: 'done' }, '승인됨'],
    [{ status: 'rejected' }, '반려됨'],
  ])('%o: the chip says %s and there is no [승인]', (facts, text) => {
    const w = mountCard({ ...base, cardState: cardStateView(facts)! })
    expect(w.get('[data-test="designer-canvas-node-card-state"]').text()).toBe(text)
    expect(w.find('[data-test="designer-canvas-node-approve"]').exists()).toBe(false)
  })

  it('a card without a run keeps the Canvas state chip; the wording follows the locale', () => {
    expect(mountCard(base).get('[data-test="designer-canvas-node-state"]').text()).toBe('진행 중')
    wrapper!.unmount()
    i18n.global.locale.value = 'en'
    const w = mountCard({ ...base, cardState: cardStateView({ status: 'review', awaitingApproval: true })!, approvable: true })
    expect(w.get('[data-test="designer-canvas-node-card-state"]').text()).toBe('Awaiting approval')
    expect(w.get('[data-test="designer-canvas-node-approve"]').text()).toBe('Approve')
    wrapper!.unmount()
    i18n.global.locale.value = 'ja'
    expect(mountCard({ ...base, cardState: cardStateView({ status: 'done' })! }).get('[data-test="designer-canvas-node-card-state"]').text()).toBe('承認済み')
  })
})

// --- The run control -----------------------------------------------------------------------------------

interface ApproveSource extends FlowGraphSource {
  calls: string[]
  run: FlowRunInfo | null
  refuse: { code: string; status: number; message: string; params?: Record<string, unknown> } | null
}

function approveSource(initial: FlowRunInfo | null): ApproveSource {
  const source: ApproveSource = {
    isSample: false,
    calls: [],
    run: initial,
    refuse: null,
    loadGraph: async () => graphOf([AWAITING, DONE]),
    loadRun: async (_p, flowId) => {
      source.calls.push(`current ${flowId}`)
      return source.run
    },
    startRun: async () => source.run!,
    stopRun: async () => source.run!,
    resumeRun: async () => source.run!,
    approveNode: async (_p, runId, nodeKey, resultDocId) => {
      source.calls.push(`approve ${runId} ${nodeKey} ${resultDocId}`)
      if (source.refuse) {
        const { code, status, message, params = {} } = source.refuse
        throw Object.assign(new Error(message), { code, status, params })
      }
      source.run = { ...source.run!, pendingPropagationCount: 1, pendingNodeKeys: [nodeKey] }
      return source.run
    },
  }
  return source
}

function control(source: ApproveSource) {
  const runs = reactive<Record<string, FlowRunState>>({})
  const refresh = vi.fn()
  const ctl = createFlowRunControl({ source, projectId: () => P, isOpen: () => true, prepare: () => true, refresh }, runs)
  return { ctl, runs, refresh }
}

// flowgate.v02.0105 T#2 (B-6): answered for a user the server allows to approve.
const STOPPED: FlowRunInfo = { runId: 'run_s', status: 'stopped', openNodeKeys: [], pendingPropagationCount: 0, pendingNodeKeys: [], viewerCanApprove: true }

describe('flowRunControl — a card’s [승인] (O1 4-1)', () => {
  it('reads the run when this tab does not know it, approves with the shown result, keeps the run it answers, reads the Canvas again', async () => {
    const source = approveSource(STOPPED)
    const { ctl, runs, refresh } = control(source)
    expect(ctl.canApprove).toBe(true)
    expect(await ctl.approve(F, 'gn_wait', TR)).toBe(true)
    expect(source.calls).toEqual([`current ${F}`, `approve run_s gn_wait ${TR}`])
    expect(runs[F]).toMatchObject({ run: { runId: 'run_s', pendingPropagationCount: 1, pendingNodeKeys: ['gn_wait'] }, approving: null, error: null, action: null })
    expect(refresh).toHaveBeenCalledWith(F)
    // Known now: the next approval goes straight to it.
    await ctl.approve(F, 'gn_other', null)
    expect(source.calls.slice(2)).toEqual(['approve run_s gn_other null'])
  })

  it('one approval per Flow at a time; the run buttons’ action is not touched', async () => {
    const source = approveSource(STOPPED)
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    const original = source.approveNode!
    source.approveNode = async (...args) => {
      await gate
      return original(...args)
    }
    const { ctl, runs } = control(source)
    const first = ctl.approve(F, 'gn_wait', TR)
    await flushPromises()
    expect(runs[F]).toMatchObject({ approving: 'gn_wait', action: null })
    expect(await ctl.approve(F, 'gn_other', null)).toBe(false)
    release()
    expect(await first).toBe(true)
    expect(runs[F].approving).toBeNull()
  })

  it.each([
    ['result_changed', 409, 'changed', 'gui.designer.center.approval.failure.result_changed'],
    ['group_ai_running', 409, 'busy', 'gui.designer.center.approval.failure.group_ai_running'],
    ['document_approval_refused', 409, 'refused', 'gui.designer.center.approval.failure.document_approval_refused'],
    ['approval_forbidden', 403, 'forbidden', 'gui.designer.center.approval.failure.approval_forbidden'],
  ])('a refusal (%s) is the Flow’s run error of kind approval, the run and the Canvas read again', async (code, status, kind, labelKey) => {
    const source = approveSource(STOPPED)
    source.refuse = { code, status, message: '서버 문구' }
    const { ctl, runs, refresh } = control(source)
    expect(await ctl.approve(F, 'gn_wait', TR)).toBe(false)
    expect(runs[F].error).toMatchObject({ kind: 'approval', code, status, message: '서버 문구', approval: { kind, labelKey } })
    expect(runs[F].approving).toBeNull()
    expect(source.calls.filter((call) => call.startsWith('current'))).toHaveLength(2)
    expect(refresh).toHaveBeenCalledWith(F)
  })

  it('a refused document keeps what its params name (the document, why, the documents approved before it)', async () => {
    const source = approveSource(STOPPED)
    source.refuse = {
      code: 'document_approval_refused',
      status: 409,
      message: '결과 레포트 검증 실패',
      params: { doc_id: TR, reason: 'tr2_validation_failed', detail: 'missing changed files', approved_doc_ids: [`${F}.0004-T`] },
    }
    const { ctl, runs } = control(source)
    expect(await ctl.approve(F, 'gn_wait', TR)).toBe(false)
    expect(runs[F].error).toMatchObject({
      kind: 'approval',
      code: 'document_approval_refused',
      message: '결과 레포트 검증 실패',
      approval: { kind: 'refused' },
      refusal: { docId: TR, reason: 'tr2_validation_failed', detail: 'missing changed files', approvedDocIds: [`${F}.0004-T`], resultDocId: null },
    })
    // A refusal naming nothing keeps an empty refusal; a run button refusal none.
    source.refuse = { code: 'group_ai_running', status: 409, message: 'busy' }
    await ctl.approve(F, 'gn_wait', TR)
    expect(runs[F].error?.refusal).toEqual({ docId: null, reason: null, detail: null, approvedDocIds: [], resultDocId: null })
  })

  it('no running or stopped run: refused as run_not_active, nothing approved', async () => {
    const source = approveSource(null)
    const { ctl, runs } = control(source)
    expect(await ctl.approve(F, 'gn_wait', TR)).toBe(false)
    expect(source.calls.some((call) => call.startsWith('approve'))).toBe(false)
    expect(runs[F].error).toMatchObject({ kind: 'approval', code: 'run_not_active', approval: { kind: 'inactive' } })
  })

  it('a run button refusal keeps kind run (no approval grouping)', async () => {
    const source = approveSource(null)
    source.startRun = async () => {
      throw Object.assign(new Error('x'), { code: 'run_policy_undecided', status: 409 })
    }
    const { ctl, runs } = control(source)
    await ctl.run(F, 'all')
    expect(runs[F].error).toMatchObject({ kind: 'run', code: 'run_policy_undecided', approval: null, refusal: null })
  })

  it('a source that cannot approve offers nothing', async () => {
    const source = approveSource(STOPPED)
    delete source.approveNode
    const { ctl } = control(source)
    expect(ctl.canApprove).toBe(false)
    expect(await ctl.approve(F, 'gn_wait', TR)).toBe(false)
  })
})

// --- The Center session and the Canvas --------------------------------------------------------------------

describe('the Center session approves a card with the result its Canvas shows', () => {
  let wrapper: VueWrapper<any> | null = null
  let center: DesignerCenterSession | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    center?.dispose()
    center = null
  })

  async function open(source: ApproveSource) {
    center = createDesignerCenterSession({ source })
    center.setProject(P)
    wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
    center.openFlow({ projectId: P, flowId: F, flowName: 'F2-1' })
    for (let i = 0; i < 4; i += 1) await flushPromises()
    return center
  }

  it('approveNode sends the shown result of the card; a card without a run is not sent', async () => {
    const source = approveSource(STOPPED)
    const session = await open(source)
    expect(session.canApprove).toBe(true)
    expect(await session.approveNode(F, 'gn_wait')).toBe(true)
    expect(source.calls).toContain(`approve run_s gn_wait ${TR}`)
    expect(await session.approveNode(F, 'gn_missing')).toBe(false)
  })

  it('a user the server does not allow sees the state and no [승인] (B-6)', async () => {
    const source = approveSource({ ...STOPPED, viewerCanApprove: false })
    const session = await open(source)
    expect(session.canApproveFlow(F)).toBe(false)
    const waiting = wrapper!.findAll('[data-test="designer-canvas-node"]').find((item) => item.attributes('data-node-id') === 'gn_wait')!
    expect(waiting.get('[data-test="designer-canvas-node-card-state"]').attributes('data-state')).toBe('awaiting_approval')
    expect(waiting.find('[data-test="designer-canvas-node-approve"]').exists()).toBe(false)
  })

  it('the Canvas card’s [승인] reaches the session; the done card has none', async () => {
    const source = approveSource(STOPPED)
    await open(source)
    const cards = wrapper!.findAll('[data-test="designer-canvas-node"]')
    const waiting = cards.find((item) => item.attributes('data-node-id') === 'gn_wait')!
    const done = cards.find((item) => item.attributes('data-node-id') === 'gn_done')!
    expect(waiting.get('[data-test="designer-canvas-node-card-state"]').text()).toBe('승인 대기')
    expect(done.get('[data-test="designer-canvas-node-card-state"]').text()).toBe('승인됨')
    expect(done.find('[data-test="designer-canvas-node-approve"]').exists()).toBe(false)
    await waiting.get('[data-test="designer-canvas-node-approve"]').trigger('click')
    for (let i = 0; i < 4; i += 1) await flushPromises()
    expect(source.calls).toContain(`approve run_s gn_wait ${TR}`)
    // The click is the card's [승인], not a selection.
    expect(center!.activeView.value?.selectedNodeId ?? null).toBeNull()
  })
})

// --- The run band ---------------------------------------------------------------------------------------

describe('the run band: 멈춤 · 다음 카드 N개 대기 (O1 4-2)', () => {
  it.each([
    [{ status: 'stopped', pendingPropagationCount: 2, pendingNodeKeys: ['gn_a', 'gn_b'] }, { count: 2, nodeKeys: ['gn_a', 'gn_b'] }],
    [{ status: 'stopped', pendingPropagationCount: 0, pendingNodeKeys: [] }, null],
    [{ status: 'stopped' }, null],
    [{ status: 'running', pendingPropagationCount: 2, pendingNodeKeys: ['gn_a'] }, null],
    [null, null],
  ] as const)('runPendingView(%o) = %o', (run, expected) => {
    expect(runPendingView(run)).toEqual(expected)
  })

  it('the Toolbar says it beside the stopped dot, the waiting cards in its tooltip; nothing on a running run', async () => {
    i18n.global.locale.value = 'ko'
    const pending = { count: 2, nodeKeys: ['gn_a', 'gn_b'] }
    const toolbar = mount(DesignerToolbar, { props: { runMode: null, density: 'normal', zoom: 100, runStatus: 'stopped', runPending: pending }, global: { plugins: [i18n] } })
    const line = toolbar.get('[data-test="designer-run-pending"]')
    expect(line.text()).toBe('멈춤 · 다음 카드 2개 대기')
    expect(line.attributes('title')).toBe('다음으로 넘어갈 카드: gn_a, gn_b')
    expect(toolbar.get('[data-test="designer-run-status"]').attributes('data-status')).toBe('stopped')
    await toolbar.setProps({ runStatus: 'running' })
    expect(toolbar.find('[data-test="designer-run-pending"]').exists()).toBe(false)
    await toolbar.setProps({ runStatus: 'stopped', disabled: true })
    expect(toolbar.find('[data-test="designer-run-pending"]').exists()).toBe(false)
    toolbar.unmount()
    i18n.global.locale.value = 'en'
    const en = mount(DesignerToolbar, { props: { runMode: null, density: 'normal', zoom: 100, runStatus: 'stopped', runPending: { count: 1, nodeKeys: [] } }, global: { plugins: [i18n] } })
    expect(en.get('[data-test="designer-run-pending"]').text()).toBe('Stopped · 1 next card(s) waiting')
    expect(en.get('[data-test="designer-run-pending"]').attributes('title')).toBeUndefined()
    en.unmount()
    i18n.global.locale.value = 'ko'
  })
})
