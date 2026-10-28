/**
 * flowgate.v02.0105 T#2 — the card's approval wait told apart and [승인] only for a user the server allows
 * (NR flowgate.v02.0101.0003 §3.9 B-2 / B-5 / B-6 / B-7, regression cases §3.10 표시·버튼):
 *
 * - cardStateView: a card awaiting approval whose automatic approval was missed or failed (X: the server's B-1 reason)
 *   is 자동 승인 실패, one a person must judge (H) or with no reason stays 승인 대기; both keep [승인] for an allowed user,
 *   the reason itself is told in the tooltip only;
 * - [승인] (the Canvas card and the Inspector bar) only when the run read's `viewer.can_approve` is true — absent (an older
 *   server), not read or false hides it; it carries the shield glyph;
 * - the Inspector pill: a card state label takes no document review sub (pending_review / revised / approved), 반려됨 stays;
 * - the transport keeps `approval_wait_reason` and `viewer.can_approve` only when sent.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import { toFlowRun } from '@/features/project/flowRunApi'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { toVueFlowNodes, type FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import { CARD_APPROVE_ICON, CARD_STATE_KEYS, cardStateView, type CardRunFacts } from '@/features/designer/center/graph/cardStateView'
import type { FlowGraph, FlowGraphNode, FlowGraphNodeRun } from '@/features/designer/center/graph/flowGraph'
import { createServerFlowGraphSource, type FlowCanvasLoaders } from '@/features/designer/center/graph/serverFlowGraphSource'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { cardApprovalBar } from '@/features/designer/inspector/features/cardApproval/cardApprovalModel'
import { GRAPH_GRAPH, selectionOf, targetOf } from './inspector/support/inspectorSupport'

registerAppMessages(i18n)

const awaiting = (reason?: string | null): CardRunFacts => ({
  status: 'review',
  awaitingApproval: true,
  runningExecutionCount: 0,
  requestedExecutionCount: 0,
  ...(reason !== undefined ? { approvalWaitReason: reason } : {}),
})

describe('cardStateView — a missed automatic approval and a person’s judgement are told apart (B-5)', () => {
  it.each([
    // reason (B-1, the server's)                   key                      tone
    ['issuer_lacks_approve', 'auto_approval_failed', 'danger'],
    ['issuer_missing', 'auto_approval_failed', 'danger'],
    ['approval_refused', 'auto_approval_failed', 'danger'],
    ['review_not_started', 'auto_approval_failed', 'danger'],
    ['review_result_unknown', 'auto_approval_failed', 'danger'],
    ['review_not_passed:review_count_exhausted', 'awaiting_approval', 'warning'],
    ['review_not_passed:review_verdict_hold', 'awaiting_approval', 'warning'],
    ['run_not_clean:timeout', 'awaiting_approval', 'warning'],
    ['a_reason_this_screen_does_not_know', 'awaiting_approval', 'warning'],
  ] as const)('%s -> %s', (reason, key, tone) => {
    const view = cardStateView(awaiting(reason))!
    expect([view.key, view.phase, view.tone, view.approvable, view.reason]).toEqual([key, 'awaiting', tone, true, reason])
  })

  it('no reason (none recorded, or an older server) stays 승인 대기 with no tooltip reason', () => {
    for (const facts of [awaiting(), awaiting(null)]) {
      const view = cardStateView(facts)!
      expect([view.key, view.reason]).toEqual(['awaiting_approval', null])
    }
  })

  it('a card not awaiting approval tells no reason, whatever was recorded', () => {
    const view = cardStateView({ status: 'review', awaitingApproval: false, approvalWaitReason: 'approval_refused' })!
    expect([view.key, view.reason, view.approvable]).toEqual(['working', null, false])
    expect(cardStateView({ status: 'done', approvalWaitReason: 'approval_refused' })!.key).toBe('approved')
  })

  it('the new state has its short word in every locale (ko: 자동 승인 실패)', () => {
    expect(CARD_STATE_KEYS).toContain('auto_approval_failed')
    expect(ko.designer.center.card_states.auto_approval_failed).toBe('자동 승인 실패')
    for (const messages of [ko, en, ja]) {
      expect(Object.keys(messages.designer.center.card_states).sort()).toEqual([...CARD_STATE_KEYS].sort())
    }
  })
})

describe('[승인] only when the server allows the user (B-6)', () => {
  const failed = cardStateView(awaiting('approval_refused'))
  const person = cardStateView(awaiting('review_not_passed:review_count_exhausted'))

  it.each([
    ['not told (an older server)', undefined],
    ['the read failed / not read', undefined],
    ['the server says no', false],
  ] as const)('%s: the bar names the state and shows no [승인]', (_label, canApprove) => {
    for (const state of [failed, person]) {
      expect(cardApprovalBar({ state, readOnly: null, busy: false, ...(canApprove !== undefined ? { canApprove } : {}) })).toEqual({
        shown: true,
        approveVisible: false,
        enabled: false,
        reason: null,
      })
    }
  })

  it('the server says yes: shown and usable; a conflict or an approval in flight only then disables it', () => {
    expect(cardApprovalBar({ state: failed, readOnly: null, busy: false, canApprove: true })).toEqual({ shown: true, approveVisible: true, enabled: true, reason: null })
    expect(cardApprovalBar({ state: person, readOnly: 'conflict', busy: false, canApprove: true }).reason).toBe('reason.read_only_conflict')
    expect(cardApprovalBar({ state: person, readOnly: null, busy: true, canApprove: true }).reason).toBe('reason.busy')
    expect(cardApprovalBar({ state: cardStateView({ status: 'done' }), readOnly: null, busy: false, canApprove: true }).approveVisible).toBe(false)
  })

  it('the Inspector target carries what the Center session confirmed (absent: no)', () => {
    const graph = graphWith(nodeRun('review', { awaitingApproval: true }))
    expect(targetOf(selectionOf(graph, graph.nodes[0].id)).canApprove).toBe(false)
    expect(targetOf({ ...selectionOf(graph, graph.nodes[0].id), canApprove: true }).canApprove).toBe(true)
  })
})

const nodeRun = (status: string, fields: Partial<FlowGraphNodeRun> = {}): FlowGraphNodeRun => ({ status, openedAt: null, finishedAt: null, resultDocId: 'p.ui.0105.0081-TR', ...fields })

function card(id: string, runFacts: FlowGraphNodeRun): FlowGraphNode {
  return {
    id,
    kind: 'task',
    title: `카드 ${id}`,
    state: 'in_progress',
    docType: 'T',
    x: 0,
    y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: id, assignee: null },
    nodeRun: runFacts,
  }
}

function graphWith(runFacts: FlowGraphNodeRun, docReviewStatus: string | null = null): FlowGraph {
  const document = docReviewStatus
    ? { docId: 'p.ui.0105.0081-TR', typeCode: 'TR', seq: 81, title: '결과', docReviewStatus, status: 'open', supersedes: [] }
    : null
  return { ...GRAPH_GRAPH, nodes: [{ ...GRAPH_GRAPH.nodes[0], state: 'in_progress', document, nodeRun: runFacts }] }
}

describe('the Canvas card: [승인] with the shield, the reason in the tooltip (B-5 / B-6 / B-7)', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    i18n.global.locale.value = 'ko'
  })

  const graph: FlowGraph = {
    flowId: 'p.ui.0105',
    nodes: [card('gn_failed', nodeRun('review', { awaitingApproval: true, approvalWaitReason: 'approval_refused' }))],
    edges: [],
    ownership: 'graph',
    viewMode: 'revision',
  }
  const dataOf = (canApprove: boolean): FlowGateNodeData => toVueFlowNodes(graph, null, new Map(), { canApprove, approvingNodeId: null })[0].data!

  it('offers [승인] only when the Flow’s cards may be approved by the user', () => {
    expect(dataOf(false).approvable).toBeUndefined()
    expect(dataOf(true).approvable).toBe(true)
    expect(dataOf(false).cardState?.key).toBe('auto_approval_failed')     // the state is shown either way
  })

  it('the chip names 자동 승인 실패 and tells why in its tooltip; [승인] carries the shield', () => {
    i18n.global.locale.value = 'ko'
    wrapper = mount(FlowNodeCard, { props: { id: 'gn_failed', data: dataOf(true) }, global: { plugins: [i18n], stubs: { Handle: true } } })
    const chip = wrapper.get('[data-state="auto_approval_failed"]')
    expect(chip.attributes('title')).toBe('자동 승인 실패 (approval_refused)')
    const button = wrapper.get('[data-test="designer-canvas-node-approve"]')
    expect(button.text()).toBe('승인')
    expect(button.get('[data-test="designer-canvas-node-approve-icon"] path').attributes('d')).toBe(CARD_APPROVE_ICON)
  })

  it('without the user’s approval no button is drawn at all', () => {
    wrapper = mount(FlowNodeCard, { props: { id: 'gn_failed', data: dataOf(false) }, global: { plugins: [i18n], stubs: { Handle: true } } })
    expect(wrapper.find('[data-test="designer-canvas-node-approve"]').exists()).toBe(false)
  })
})

describe('the Inspector pill: the card state alone, the reason in the tooltip (B-5)', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })
  const pillOf = (graph: FlowGraph) => {
    wrapper?.unmount()
    wrapper = mount(InspectorHeader, { props: { target: targetOf(selectionOf(graph, graph.nodes[0].id)) }, global: { plugins: [i18n] } })
    const pill = wrapper.get('[data-test="designer-inspector-pill"]')
    const sub = wrapper.find('[data-test="designer-inspector-pill-sub"]')
    return {
      label: wrapper.get('[data-test="designer-inspector-pill-label"]').text(),
      sub: sub.exists() ? sub.text() : null,
      doc: pill.attributes('data-doc-status'),
      title: pill.attributes('title') ?? '',
    }
  }

  it.each(['pending_review', 'revised', 'approved'])('승인 대기 + document %s: no 검수 sub; the document state stays in the DOM and tooltip', (status) => {
    i18n.global.locale.value = 'ko'
    const pill = pillOf(graphWith(nodeRun('review', { awaitingApproval: true }), status))
    expect([pill.label, pill.sub, pill.doc]).toEqual(['승인 대기', null, status])
    expect(pill.title).toContain(`(${status})`)
  })

  it('a rejected document keeps its sub', () => {
    i18n.global.locale.value = 'ko'
    const pill = pillOf(graphWith(nodeRun('review', { awaitingApproval: true }), 'rejected'))
    expect(pill.label).toBe('승인 대기')
    expect(pill.sub).toContain('반려')
  })

  it('자동 승인 실패 with its reason in the tooltip only', () => {
    i18n.global.locale.value = 'ko'
    const pill = pillOf(graphWith(nodeRun('review', { awaitingApproval: true, approvalWaitReason: 'review_not_started' }), 'pending_review'))
    expect([pill.label, pill.sub]).toEqual(['자동 승인 실패', null])
    expect(pill.title).toContain('자동 승인 실패 (review_not_started)')
  })
})

describe('the transport keeps the reason and the viewer only when sent (B-2 / B-6)', () => {
  const raw = (extra: Record<string, unknown> = {}) => ({
    run_id: 'run_1',
    flow_id: 'p.ui.0105',
    status: 'idle',
    node_runs: [{ node_key: 'gn_1', status: 'review', result_doc_id: 'd', awaiting_approval: true, approval_wait_reason: 'approval_refused' }],
    ...extra,
  })

  it('maps viewer.can_approve true / false and leaves it out when absent', () => {
    expect(toFlowRun(raw({ viewer: { can_approve: true } }))!.viewerCanApprove).toBe(true)
    expect(toFlowRun(raw({ viewer: { can_approve: false } }))!.viewerCanApprove).toBe(false)
    expect('viewerCanApprove' in toFlowRun(raw())!).toBe(false)
    expect('viewerCanApprove' in toFlowRun(raw({ viewer: { can_approve: 'yes' } }))!).toBe(false)
  })

  it('maps each node run’s approval_wait_reason', () => {
    expect(toFlowRun(raw())!.nodeRuns![0].approvalWaitReason).toBe('approval_refused')
    const none = toFlowRun({ ...raw(), node_runs: [{ node_key: 'gn_1', status: 'review' }] })!
    expect('approvalWaitReason' in none.nodeRuns![0]).toBe(false)
  })

  it('the server Graph source hands the viewer on with the run (absent stays absent)', async () => {
    const loaders = (run: ReturnType<typeof toFlowRun>) =>
      ({
        loadFlowCanvas: async () => {
          throw new Error('not read here')
        },
        saveFlowCanvasLayout: async () => ({ ignored: [] }),
        loadFlowRun: async () => run,
        startFlowRun: async () => run!,
        stopFlowRun: async () => run!,
        resumeFlowRun: async () => run!,
      }) as unknown as FlowCanvasLoaders
    const allowed = await createServerFlowGraphSource(loaders(toFlowRun(raw({ viewer: { can_approve: true } })))).loadRun!('p', 'p.ui.0105')
    expect(allowed!.viewerCanApprove).toBe(true)
    const older = await createServerFlowGraphSource(loaders(toFlowRun(raw()))).loadRun!('p', 'p.ui.0105')
    expect(older && 'viewerCanApprove' in older).toBe(false)
  })
})
