/**
 * flowgate.v02.0048 T#11 — local helper of the test section's specs (D#1 §12.3: a feature keeps its own
 * helpers in its own folder). Server-shaped `/test-document` answers (test_run_service
 * describe_test_document / shape_run) for a specification TS, its TSR and a legacy TS, and Canvas
 * selections of TS / TSR / V Nodes in each ownership, built from the shared support fixtures.
 */
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import { CONFLICT_GRAPH, GRAPH_GRAPH, UNASSIGNED_GRAPH, V01_GRAPH, selectionOf } from '../support/inspectorSupport'

export const TS_DOC = 'flowgate.v02.0048.0031-TS'
export const TSR_DOC = 'flowgate.v02.0048.0032-TSR'
export const V_DOC = 'flowgate.v02.0048.0033-V'
export const LEGACY_TS_DOC = 'flowgate.v02.0048.0034-TS'

export const counts = (pass: number, fail: number, blocked = 0, notRun = 0) => ({ total: pass + fail + blocked + notRun, pass, fail, blocked, not_run: notRun })

/** One shape_run record of a server spec execution. */
export function runRow(runId: string, fields: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: runId,
    revision_no: 0,
    status: 'passed',
    triggered_via: 'ui',
    runner_id: 'u1',
    case_total: 2,
    case_passed: 2,
    case_failed: 0,
    error: null,
    tsr_doc_id: TSR_DOC,
    started_at: '2026-10-09T10:00:00Z',
    finished_at: '2026-10-09T10:02:00Z',
    created_at: '2026-10-09T10:00:00Z',
    contract_version: 2,
    overall: 'PASS',
    summary: { counts: counts(2, 0, 0, 1) },
    run_kind: 'spec_execution',
    phase: 'finished',
    selected_case_ids: ['TC-1', 'TC-2'],
    reported_case_ids: ['TC-1', 'TC-2'],
    prepare_refused: null,
    ...fields,
  }
}

export const CASES = [
  { case_id: 'TC-1', title: '오름차순', execution_mode: 'automated', required: true },
  { case_id: 'TC-2', title: '빈 목록', execution_mode: 'automated', required: true },
  { case_id: 'TC-3', title: '화면 확인', execution_mode: 'manual', required: false },
]

export const CAPABILITIES = { 'TC-1': 'case_selectable', 'TC-2': 'suite_only', 'TC-3': 'none' }

/** describe_test_document of a contract-2 TS (approved, one PASS result, no live run). */
export function tsDocument(fields: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    kind: 'TS',
    doc_id: TS_DOC,
    contract_version: 2,
    title: '정렬 테스트',
    cases: CASES,
    errors: [],
    latest_result: runRow('tr_1'),
    doc_review_status: 'approved',
    tsr_doc_id: TSR_DOC,
    tsr_review_status: 'pending_review',
    run_history: [runRow('tr_1')],
    last_execution: runRow('tr_1'),
    active_run: null,
    case_capabilities: CAPABILITIES,
    basis_valid: true,
    basis_verdict: { state: 'valid', reasons: [] },
    effective_result: { summary: { overall: 'PASS', counts: counts(2, 0, 0, 1) }, cases: [] },
    progress: { automated_total: 2, automated_completed: 2 },
    ...fields,
  }
}

/** describe_test_document of the TSR assembled from that TS. */
export function tsrDocument(fields: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    kind: 'TSR',
    doc_id: TSR_DOC,
    contract_version: 2,
    target_ts: TS_DOC,
    report: runRow('tr_1'),
    gate: { applies: true, passed: true, overall: 'PASS' },
    doc_review_status: 'pending_review',
    run_history: [runRow('tr_1')],
    active_run: null,
    last_execution: runRow('tr_1'),
    basis_valid: true,
    basis_verdict: { state: 'valid', reasons: [] },
    effective_result: { summary: { overall: 'PASS', counts: counts(2, 0, 0, 1) }, cases: [] },
    ts_review_status: 'approved',
    ...fields,
  }
}

/** describe_test_document of a legacy executable TS. */
export function legacyDocument(): Record<string, unknown> {
  return { ok: true, kind: 'TS', doc_id: LEGACY_TS_DOC, contract_version: 1, doc_title: '실행형 테스트', can_start_spec: false }
}

/** A live run (phase, reported Cases) as active_run / run_history[0] carry it. */
export const liveRun = (runId: string, fields: Record<string, unknown> = {}) =>
  runRow(runId, { status: 'running', phase: 'executing', overall: null, summary: null, finished_at: null, reported_case_ids: ['TC-1'], ...fields })

function docNode(id: string, docId: string, typeCode: string, title: string): FlowGraphNode {
  return {
    id,
    kind: 'test',
    title,
    state: 'in_progress',
    docType: typeCode,
    x: 0,
    y: 0,
    document: { docId, typeCode, seq: Number(docId.slice(-7, -3)) || 1, title, docReviewStatus: 'approved', status: 'open', supersedes: [] },
    slot: null,
    nodeRun: null,
  }
}

export const TS_NODE = docNode(`doc:${TS_DOC}`, TS_DOC, 'TS', '정렬 테스트')
export const TSR_NODE = docNode(`doc:${TSR_DOC}`, TSR_DOC, 'TSR', '정렬 테스트 결과')
export const V_NODE = docNode(`doc:${V_DOC}`, V_DOC, 'V', '검증')
export const LEGACY_TS_NODE = docNode(`doc:${LEGACY_TS_DOC}`, LEGACY_TS_DOC, 'TS', '실행형 테스트')

/** A copy of a shared fixture Flow holding the test Nodes. */
export function withTestNodes(graph: FlowGraph): FlowGraph {
  return { ...graph, nodes: [...graph.nodes, TS_NODE, TSR_NODE, V_NODE, LEGACY_TS_NODE] }
}

export const TEST_FLOWS = {
  v01: withTestNodes(V01_GRAPH),
  graph: withTestNodes(GRAPH_GRAPH),
  graphRevision: withTestNodes({ ...GRAPH_GRAPH, viewMode: 'revision' }),
  unassigned: withTestNodes(UNASSIGNED_GRAPH),
  conflict: withTestNodes(CONFLICT_GRAPH),
} as const

export const testSelection = (flow: keyof typeof TEST_FLOWS, node: FlowGraphNode): FlowGraphSelection => selectionOf(TEST_FLOWS[flow], node.id)
