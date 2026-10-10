/**
 * flowgate.v02.0048 T#13 — local helper of the WP summary's specs (D#1 §12.3: a feature keeps its own helpers
 * in its own folder). Server-shaped `GET /documents/{doc_id}/work-plan` answers (work_plan.py _read_view: body
 * steps, step_execution_status, assignment_summary, totals) and Canvas selections of a WP Node in each
 * ownership, built from the shared support fixtures.
 */
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import { CONFLICT_GRAPH, GRAPH_GRAPH, UNASSIGNED_GRAPH, V01_GRAPH, selectionOf } from '../support/inspectorSupport'

export const WP_DOC = 'flowgate.v02.0048.0006-WP'
export const OTHER_WP_DOC = 'flowgate.v02.0048.0004-WP'

/** One canonical Step (work_plan_service.make_step) with the given fields. */
export function stepRow(type: string, ordinal: number, fields: Record<string, unknown> = {}): Record<string, unknown> {
  const key = `${type}#${ordinal}`
  return {
    key,
    card_id: key,
    type,
    ordinal,
    pair_key: null,
    pair_role: 'single',
    provider_id: null,
    provider_display_name: null,
    note: null,
    review_count: 0,
    reviewer_provider_id: null,
    reviewer_provider_display_name: null,
    pre_instruction_text: null,
    pre_instruction_attachment: null,
    locked: false,
    locked_reason: null,
    origin: 'human',
    ...fields,
  }
}

/** An instruction / result pair sharing the instruction's card. */
export function pairRows(type: string, result: string, ordinal: number, fields: Record<string, unknown> = {}): Record<string, unknown>[] {
  const key = `${type}#${ordinal}`
  const resultKey = `${result}#${ordinal}`
  return [
    stepRow(type, ordinal, { pair_key: resultKey, pair_role: 'instruction', ...fields }),
    stepRow(result, ordinal, { card_id: key, pair_key: key, pair_role: 'result', ...fields }),
  ]
}

/** The default plan: D#1 (claude), D#2 (unassigned), T#1→TR#1 (codex, review 1), T#2→TR#2 (claude). */
export function planSteps(): Record<string, unknown>[] {
  return [
    stepRow('D', 1, { provider_id: 'claude', provider_display_name: 'Claude (old)' }),
    stepRow('D', 2),
    ...pairRows('T', 'TR', 1, { provider_id: 'codex', provider_display_name: 'Codex', review_count: 1 }),
    ...pairRows('T', 'TR', 2, { provider_id: 'claude', provider_display_name: 'Claude (old)' }),
  ]
}

/** _read_view of a WP document; `started` lists the cards the workflow already started. */
export function workPlanDocument(fields: Record<string, unknown> = {}, started: readonly string[] = ['D#1']): Record<string, unknown> {
  const steps = (fields.steps as Record<string, unknown>[] | undefined) ?? planSteps()
  const rest = { ...fields }
  delete rest.steps
  return {
    ok: true,
    doc_id: WP_DOC,
    doc_type: 'WP',
    title: '작업계획 — 설계 2장 · 작업 2세트',
    group_id: 'flowgate.v02.0048',
    parent_doc_id: 'flowgate.v02.0048.0001-R',
    status: 'open',
    doc_review_status: 'approved',
    editable: true,
    edit_locked_reason: null,
    revision_no: 3,
    stored_path: 'documents/flowgate/main/v02/0048/0006-WP_document.json',
    origin: 'human',
    origin_run_id: null,
    created_by: 'u1',
    updated_by: 'u1',
    updated_at: '2026-10-09T10:00:00+09:00',
    body: { wp_version: 2, binding: 'advisory', counted_types: ['D', 'T'], quantities: { D: { count: 2 }, T: { count: 2 } }, steps },
    registered_providers: [
      { id: 'claude', name: 'Claude', group_label: 'AI' },
      { id: 'codex', name: 'Codex', group_label: 'AI' },
    ],
    provider_status: [],
    step_execution_status: steps.map((step) => ({ step_key: step.key, card_id: step.card_id, started: started.includes(String(step.card_id)) })),
    review_count_choices: [0, 1, 2, 3],
    assignment_summary: [
      { provider_id: 'claude', display_name: 'Claude', step_count: 3 },
      { provider_id: 'codex', display_name: 'Codex', step_count: 2 },
    ],
    unassigned_step_count: 1,
    totals: { design_sheets: 2, work_sets: 2, steps: steps.length },
    limits: { note_max_chars: 500, pre_instruction_text_max_chars: 20000, pre_instruction_attachment_max_bytes: 1 },
    last_application: null,
    ...rest,
  }
}

function wpNode(docId: string, title: string): FlowGraphNode {
  return {
    id: `doc:${docId}`,
    kind: 'plan',
    title,
    state: 'done',
    docType: 'WP',
    x: 0,
    y: 0,
    document: { docId, typeCode: 'WP', seq: Number(docId.slice(-7, -3)) || 1, title, docReviewStatus: 'approved', status: 'open', supersedes: [] },
    slot: null,
    nodeRun: null,
  }
}

export const WP_NODE = wpNode(WP_DOC, '작업계획 — 설계 2장 · 작업 2세트')
export const OTHER_WP_NODE = wpNode(OTHER_WP_DOC, '작업계획 — 이전 계획')
/** A WP Node of a sequence slot with no document yet. */
export const WP_EMPTY_NODE: FlowGraphNode = { ...wpNode('none', '작업계획'), id: 'slot_wp', document: null }

/** A copy of a shared fixture Flow holding the WP Nodes. */
export function withPlanNodes(graph: FlowGraph): FlowGraph {
  return { ...graph, nodes: [...graph.nodes, WP_NODE, OTHER_WP_NODE, WP_EMPTY_NODE] }
}

export const PLAN_FLOWS = {
  v01: withPlanNodes(V01_GRAPH),
  graph: withPlanNodes(GRAPH_GRAPH),
  graphRevision: withPlanNodes({ ...GRAPH_GRAPH, viewMode: 'revision' }),
  unassigned: withPlanNodes(UNASSIGNED_GRAPH),
  conflict: withPlanNodes(CONFLICT_GRAPH),
} as const

export const planSelection = (flow: keyof typeof PLAN_FLOWS, node: FlowGraphNode): FlowGraphSelection => selectionOf(PLAN_FLOWS[flow], node.id)
