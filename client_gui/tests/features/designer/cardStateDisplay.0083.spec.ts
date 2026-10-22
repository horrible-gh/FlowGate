/**
 * flowgate.v02.0083 T#4 — the run band and the card state show what the server already tells apart (0022-T 작업 2·4):
 *
 * - cardStateView: an open card no AI execution works on (running / requested executions both 0) waits — 응답 대기
 *   with an unanswered question, else 작성 대기 — never 작업 중; a writer that ended without a result keeps 결과 없음 /
 *   실행 미완료; an AI that runs or is requested keeps 결과 작성 중; without the counts the card keeps 작업 중 as before.
 * - the Canvas card: a waiting card shows the clock, not the spinning 작업 중 mark.
 * - run state remains available internally; the toolbar has no status area in any phase (T0024 revision).
 * - the Inspector pill: a Graph card with a run leads with its card state — the Canvas card's word, not 진행 중.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { toVueFlowNodes, type FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import { cardStateView, type CardRunFacts } from '@/features/designer/center/graph/cardStateView'
import type { FlowGraph, FlowGraphNode, FlowGraphNodeRun } from '@/features/designer/center/graph/flowGraph'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { runBandTone, runBandView, type DesignerRunFacts } from '@/features/designer/top/designerTop'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import { GRAPH_GRAPH, selectionOf, targetOf } from './inspector/support/inspectorSupport'

registerAppMessages(i18n)

const run = (status: string | null, fields: Partial<CardRunFacts> = {}): CardRunFacts => ({ status, ...fields })
const idle = { runningExecutionCount: 0, requestedExecutionCount: 0 }

/** Compact row: key · phase · tone. */
const row = (facts: CardRunFacts) => {
  const view = cardStateView(facts)
  return view ? [view.key, view.phase, view.tone] : null
}

describe('cardStateView — working, waiting and failed are told apart (0083 T#4)', () => {
  it.each([
    // node run (as the Canvas / run read sends it)                                       key               phase       tone
    ['AI writing: a running execution', run('open', { stage: 'result', runningExecutionCount: 1, requestedExecutionCount: 0 }), ['writing_result', 'working', 'info']],
    ['AI about to start: a requested execution', run('open', { stage: 'result', runningExecutionCount: 0, requestedExecutionCount: 1 }), ['writing_result', 'working', 'info']],
    ['instruction AI writing', run('open', { stage: 'instruction', runningExecutionCount: 1 }), ['writing_instruction', 'working', 'info']],
    ['a person’s card: nothing runs', run('open', { stage: null, ...idle }), ['waiting_writer', 'awaiting', 'neutral']],
    ['an AI card whose AI is not started yet / handed back', run('open', { stage: 'result', ...idle }), ['waiting_writer', 'awaiting', 'neutral']],
    ['a question on its document waits for an answer', run('open', { stage: 'result', question: true, ...idle }), ['waiting_answer', 'awaiting', 'neutral']],
    ['the AI ended without a result', run('open', { stage: 'result', dispatchErrorCode: 'no_result', ...idle }), ['no_result', 'other', 'warning']],
    ['the AI run failed', run('open', { stage: 'result', dispatchErrorCode: 'ai_invoke_failed', ...idle }), ['incomplete', 'other', 'warning']],
    ['the AI start was refused', run('open', { stage: 'result', dispatchErrorCode: 'provider_unavailable', ...idle }), ['incomplete', 'other', 'warning']],
    ['no counts sent (an older server): as before', run('open', { stage: null }), ['working', 'working', 'info']],
    ['review: its review loop works', run('review', { awaitingApproval: false, runningExecutionCount: 1 }), ['working', 'working', 'info']],
    ['review: awaiting approval', run('review', { awaitingApproval: true, ...idle }), ['awaiting_approval', 'awaiting', 'warning']],
    ['done', run('done', idle), ['approved', 'approved', 'success']],
  ] as const)('%s', (_label, facts, expected) => {
    expect(row(facts)).toEqual(expected)
  })

  it('waiting is never approvable and both waits have ko / en / ja words', () => {
    expect(cardStateView(run('open', idle))!.approvable).toBe(false)
    expect(ko.designer.center.card_states).toMatchObject({ waiting_writer: '작성 대기', waiting_answer: '응답 대기' })
    for (const messages of [en, ja]) {
      expect(typeof messages.designer.center.card_states.waiting_writer).toBe('string')
      expect(typeof messages.designer.center.card_states.waiting_answer).toBe('string')
    }
  })
})

const nodeRun = (status: string, fields: Partial<FlowGraphNodeRun> = {}): FlowGraphNodeRun => ({ status, openedAt: null, finishedAt: null, resultDocId: null, ...fields })

function card(id: string, runFacts: FlowGraphNodeRun): FlowGraphNode {
  return {
    id,
    kind: 'plan',
    title: `카드 ${id}`,
    state: 'in_progress',
    docType: 'WP',
    x: 0,
    y: 0,
    definition: { nodeType: 'document', docType: 'WP', paletteItemId: null, label: id, assignee: null },
    nodeRun: runFacts,
  }
}

describe('the Canvas card (vueFlowMapping, FlowNodeCard)', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    i18n.global.locale.value = 'ko'
  })

  const graph: FlowGraph = {
    flowId: 'p.ui.0083',
    nodes: [
      card('gn_ai', nodeRun('open', { stage: 'result', runningExecutionCount: 1, requestedExecutionCount: 0 })),
      card('gn_person', nodeRun('open', { stage: null, runningExecutionCount: 0, requestedExecutionCount: 0 })),
      card('gn_none', nodeRun('open', { stage: 'result', dispatchErrorCode: 'no_result', runningExecutionCount: 0, requestedExecutionCount: 0 })),
    ],
    edges: [],
    ownership: 'graph',
    viewMode: 'revision',
  }

  it('each card carries its own state; the waiting card is not 작업 중', () => {
    const data = Object.fromEntries(toVueFlowNodes(graph, null).map((node) => [node.id, node.data])) as Record<string, FlowGateNodeData>
    expect(data.gn_ai.cardState?.key).toBe('writing_result')
    expect(data.gn_person.cardState?.key).toBe('waiting_writer')
    expect(data.gn_none.cardState?.key).toBe('no_result')
  })

  it('the waiting chip is the clock with its words, never the spinning mark', () => {
    i18n.global.locale.value = 'ko'
    const data: FlowGateNodeData = { kind: 'plan', title: '카드', state: 'in_progress', selected: false, cardState: cardStateView(run('open', idle))! }
    wrapper = mount(FlowNodeCard, { props: { id: 'gn_person', data }, global: { plugins: [i18n], stubs: { Handle: true } } })
    const chip = wrapper.get('[data-test="designer-canvas-node-card-state"]')
    expect(chip.attributes('data-state')).toBe('waiting_writer')
    expect(chip.attributes('data-phase')).toBe('awaiting')
    expect(chip.attributes('title')).toBe('작성 대기')
    expect(chip.find('svg.is-spinning').exists()).toBe(false)
    wrapper.unmount()
    const working: FlowGateNodeData = { ...data, cardState: cardStateView(run('open', { stage: 'result', runningExecutionCount: 1 }))! }
    wrapper = mount(FlowNodeCard, { props: { id: 'gn_ai', data: working }, global: { plugins: [i18n], stubs: { Handle: true } } })
    expect(wrapper.get('[data-test="designer-canvas-node-card-state"] svg').classes()).toContain('is-spinning')
  })
})

describe('the run band (designerTop.runBandTone, DesignerToolbar)', () => {
  const band = (facts: DesignerRunFacts) => runBandView(facts)!

  it.each([
    [{ status: 'running', mode: 'full', runningExecutionCount: 1 }, 'info'],
    [{ status: 'running', mode: 'step', runningExecutionCount: 0 }, 'info'],
    [{ status: 'idle', idleReason: 'waiting_approval' }, 'warning'],
    [{ status: 'idle', idleReason: 'hold' }, 'warning'],
    [{ status: 'idle', idleReason: 'error' }, 'danger'],
    [{ status: 'idle', idleReason: 'blocked_rejected' }, 'danger'],
    [{ status: 'idle', idleReason: 'merge_conflict' }, 'danger'],
    [{ status: 'idle', idleReason: 'completed' }, 'success'],
    [{ status: 'idle', idleReason: 'step_done' }, 'neutral'],
    [{ status: 'idle', idleReason: 'something_new' }, 'neutral'],
    [{ status: 'paused', mode: 'full' }, 'warning'],
    [{ status: 'stopping' }, 'neutral'],
  ] as const)('runBandTone(%o) = %s', (facts, tone) => {
    expect(runBandTone(band(facts))).toBe(tone)
  })

  let toolbar: VueWrapper<any> | null = null
  afterEach(() => {
    toolbar?.unmount()
    toolbar = null
    i18n.global.locale.value = 'ko'
  })
  const mountToolbar = (runBand: ReturnType<typeof runBandView>, extra: Record<string, unknown> = {}) => {
    toolbar = mount(DesignerToolbar, { props: { runPhase: runBand?.phase ?? 'idle', density: 'normal', zoom: 100, runBand, ...extra }, global: { plugins: [i18n] } })
    return toolbar
  }
  it.each([
    { status: 'idle', idleReason: 'completed' },
    { status: 'idle', idleReason: 'waiting_approval' },
    { status: 'idle', idleReason: 'error' },
    { status: 'idle', idleReason: 'blocked_rejected' },
    { status: 'idle', idleReason: 'merge_conflict' },
    { status: 'idle', idleReason: 'hold' },
    { status: 'idle', idleReason: 'step_done' },
    { status: 'running', mode: 'full', runningExecutionCount: 1, requestedExecutionCount: 1, executionLimit: 4, pendingPropagationCount: 2, pendingNodeKeys: ['gn_a'] },
    { status: 'paused' },
    { status: 'stopping' },
  ] as const)('removes the entire status area for %o while preserving run commands', async (facts) => {
    const w = mountToolbar(runBandView(facts), { canValidate: true })
    expect(w.find('[data-test="designer-run-band"]').exists()).toBe(false)
    expect(w.find('.designer-run-band, .designer-run-dot, [role="status"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-run-band-status"], [data-test="designer-run-band-detail"]').exists()).toBe(false)
    const commands = facts.status === 'running' ? ['run.pause', 'run.stop']
      : facts.status === 'paused' ? ['run.resume', 'run.stop']
      : facts.status === 'stopping' ? ['run.stop'] : ['run.next', 'run.all']
    const buttons = w.findAll('[data-test="designer-tool-run"]')
    expect(buttons.map(button => button.attributes('data-command'))).toEqual(commands)
    expect(w.find('[data-command="run.validate"]').exists()).toBe(true)
    if (facts.status === 'stopping') {
      expect(buttons[0].attributes('disabled')).toBeDefined()
    } else {
      await buttons[0].trigger('click')
      expect(w.emitted('command')).toEqual([[commands[0]]])
    }
  })

  it('has no status area without a run or work target', () => {
    expect(mountToolbar(null).find('[role="status"]').exists()).toBe(false)
    toolbar!.unmount()
    expect(mountToolbar(runBandView({ status: 'idle', idleReason: 'error' }), { disabled: true }).find('[role="status"]').exists()).toBe(false)
  })
})

describe('the Inspector pill leads with the card state (InspectorHeader)', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })
  const graphWith = (runFacts: FlowGraphNodeRun): FlowGraph => ({ ...GRAPH_GRAPH, nodes: [{ ...GRAPH_GRAPH.nodes[0], state: 'in_progress', document: null, nodeRun: runFacts }] })
  const pillOf = (runFacts: FlowGraphNodeRun) => {
    const graph = graphWith(runFacts)
    wrapper?.unmount()
    wrapper = mount(InspectorHeader, { props: { target: targetOf(selectionOf(graph, graph.nodes[0].id)) }, global: { plugins: [i18n] } })
    const pill = wrapper.get('[data-test="designer-inspector-pill"]')
    return { label: wrapper.get('[data-test="designer-inspector-pill-label"]').text(), card: pill.attributes('data-card-state'), state: pill.attributes('data-state'), tone: pill.attributes('data-tone') }
  }

  it.each([
    ['its AI writes', nodeRun('open', { stage: 'result', runningExecutionCount: 1, requestedExecutionCount: 0 }), '결과 작성 중', 'writing_result', 'info'],
    ['a person writes', nodeRun('open', { stage: null, runningExecutionCount: 0, requestedExecutionCount: 0 }), '작성 대기', 'waiting_writer', 'neutral'],
    ['the AI ended without a result', nodeRun('open', { stage: 'result', dispatchErrorCode: 'no_result', runningExecutionCount: 0, requestedExecutionCount: 0 }), '결과 없음', 'no_result', 'warning'],
    ['the AI failed', nodeRun('open', { stage: 'result', dispatchErrorCode: 'ai_invoke_failed', runningExecutionCount: 0, requestedExecutionCount: 0 }), '실행 미완료', 'incomplete', 'warning'],
  ] as const)('%s: %s', (_label, runFacts, words, key, tone) => {
    i18n.global.locale.value = 'ko'
    const pill = pillOf(runFacts)
    expect(pill.label).toBe(words)
    expect(pill.card).toBe(key)
    expect(pill.tone).toBe(tone)
    expect(pill.state).toBe('in_progress') // the Canvas state stays in the DOM, never as the words
  })

  it('a run status the card state does not know keeps the Canvas state as before', () => {
    i18n.global.locale.value = 'ko'
    const pill = pillOf(nodeRun('completed'))
    expect(pill.card).toBe('')
    expect(pill.label).toBe('진행 중')
  })
})
