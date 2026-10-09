/**
 * flowgate.v02.0048 T#12 — the check card's local test helpers (D#1 §12.3: a feature keeps its own
 * helpers in its own folder). An AC Node of a v0.1 Flow (as the Canvas projects it: a free document
 * node once the AC exists), its target in other ownerships / views, and the server shapes the mock
 * server answers with (get_finalize_state, approval_publish.response_payload, the document row).
 */
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { PROJECT_ID, V01_GRAPH, V01_TASK_NODE, selectionOf, targetOf } from '../support/inspectorSupport'

export const FLOW = 'test2.default.0034'
export const AC_DOC = `${FLOW}.0006-AC`
export const OTHER_AC_DOC = 'test2.default.0016.0004-AC'

export const AC_NODE: FlowGraphNode = {
  id: `doc:${AC_DOC}`,
  kind: 'approval',
  title: 'Final Approval',
  state: 'in_progress',
  docType: 'AC',
  x: 520,
  y: 0,
  document: {
    docId: AC_DOC,
    typeCode: 'AC',
    seq: 6,
    title: 'Final Approval',
    docReviewStatus: 'pending_review',
    status: 'draft',
    supersedes: [],
  },
  slot: null,
  nodeRun: null,
}

export const AC_GRAPH: FlowGraph = { ...V01_GRAPH, flowId: FLOW, nodes: [V01_TASK_NODE, AC_NODE] }

export const acSelection = (fields: Partial<FlowGraph> = {}, node: FlowGraphNode = AC_NODE): FlowGraphSelection =>
  selectionOf({ ...AC_GRAPH, ...fields, nodes: [V01_TASK_NODE, node] }, node.id, PROJECT_ID)

export const acTarget = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...targetOf(acSelection()), ...fields })

/** get_finalize_state of a Git active group whose AC still waits (context=approval). */
export function finalizeState(fields: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    group_id: FLOW,
    branch: 'test2_default_0034',
    base_branch: 'main',
    work_base_ref: 'main',
    default_target: 'main',
    status: 'awaiting_choice',
    default_action: 'merge',
    choices: ['merge', 'merge_only', 'wait'],
    aux_choices: ['push'],
    ahead_count: 2,
    behind_count: 0,
    merge_id: null,
    merge_commit: null,
    approval_pending: false,
    approval_in_flight: false,
    approval_job: null,
    ...fields,
  }
}

/** The empty state: not Git active, or nothing to reflect. */
export const NONE_STATE = { group_id: FLOW, branch: null, base_branch: null, status: 'none', default_action: null, choices: [], aux_choices: [], action_axes: null, ahead_count: null, behind_count: null, work_base_ref: null, merge_id: null, approval_in_flight: false, approval_job: null }

export const docRow = (docId: string, status: string, disposed = false) => ({
  doc_id: docId,
  project_id: 'test2',
  group_id: docId.split('.').slice(0, 3).join('.'),
  type_code: 'AC',
  seq: 6,
  title: 'Final Approval',
  status: 'draft',
  doc_review_status: status,
  group_disposed: disposed,
})

/** approval_publish EXECUTED: approved, with the Git result. */
export const executed = (docId: string, git: Record<string, unknown>) => ({
  ok: true,
  git: { ok: true, terminal: true, result: git },
  approval: { approved: true, document_status: 'approved', root_status: 'wf_done', stage: 'complete', deferred: false },
  job: { job_id: 'job_1', status: 'succeeded' },
  document: docRow(docId, 'approved'),
})

/** approval_publish QUEUED: the job finishes it later. */
export const queued = (jobId: string) => ({
  ok: true,
  response_status: 'queued',
  git: { ok: true, terminal: false, queued: true },
  approval: { approved: false, document_status: 'pending_review', root_status: 'wf_in_progress', deferred: true, stage: 'queued', job_id: jobId, blocker: { domain: 'merge_target' } },
  job: { job_id: jobId, status: 'blocked', stage: 'freeze_wait' },
})

/** approval_publish HANDED_OFF: Git went to a conflict review, the approval waits for it. */
export const handedOff = (files: string[]) => ({
  ok: true,
  git: { ok: true, terminal: false, deferred: true, result: { status: 'conflict', merge_id: 12, conflict_files: files } },
  approval: { approved: false, document_status: 'pending_review', root_status: 'wf_in_progress', stage: 'git_finalize', deferred: true, merge_id: 12 },
  job: { job_id: 'job_3', status: 'succeeded' },
})

export const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
export const networkError = () => ({ isAxiosError: true, message: 'Network Error' })
