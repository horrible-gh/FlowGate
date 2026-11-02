import { describe, expect, it } from 'vitest'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { NO_RUN_MARKS } from '@/features/designer/center/run/flowRunLock'
import { GRAPH_GRAPH, GRAPH_TEST_NODE, GRAPH_GATE_NODE } from './inspector/support/inspectorSupport'
const run = (extra = {}) => ({ status: 'review', openedAt: null, finishedAt: null, resultDocId: 'result', awaitingApproval: true, ...extra })
describe('v0.2 Canvas state mapping: no human approval', () => {
  it.each([true, false])('never maps approval capability (%s), even from historical wait flags', canApprove => {
    const graph = { ...GRAPH_GRAPH, nodes: [{ ...GRAPH_TEST_NODE, nodeRun: run() }] }
    const data = toVueFlowNodes(graph, null, new Map(), { canApprove, approvingNodeId: GRAPH_TEST_NODE.id })[0].data
    expect(data.cardState).toMatchObject({ key: 'needs_intervention', approvable: false })
    expect(data.approvable).toBeUndefined()
    expect(data.approving).toBeUndefined()
  })
  it('uses the latest run intervention, clearing an older Canvas intervention with explicit null', () => {
    const graph = { ...GRAPH_GRAPH, nodes: [{ ...GRAPH_TEST_NODE, nodeRun: run({ intervention: { kind: 'system_error', reason: 'issuer_missing' } }) }] }
    const marks = { ...NO_RUN_MARKS, nodeRuns: new Map([[GRAPH_TEST_NODE.id, { nodeKey: GRAPH_TEST_NODE.id, ...run({ intervention: null, awaitingApproval: false, nodeRunning: true }) }]]) }
    expect(toVueFlowNodes(graph, null, new Map(), { marks })[0].data.cardState?.key).toBe('working')
  })
  it('keeps gates outside general card states and uses gate job evidence from the latest run', () => {
    const graph = { ...GRAPH_GRAPH, nodes: [{ ...GRAPH_GATE_NODE, state: 'done', nodeRun: run({ intervention: { kind: 'system_error', reason: 'ignored' } }) }] }
    const gate = { decision: null, decision_in_progress: true, job: { status: 'running', phase_note: 'gate_conflict' } }
    const marks = { ...NO_RUN_MARKS, nodeRuns: new Map([[GRAPH_GATE_NODE.id, { nodeKey: GRAPH_GATE_NODE.id, ...run({ gate }) }]]) }
    const data = toVueFlowNodes(graph, null, new Map(), { marks: marks as any, canApprove: true })[0].data
    expect(data.cardState).toBeUndefined()
    expect(data.gateState?.key).toBe('conflict_review')
    expect(data.approvable).toBeUndefined()
  })
})
