// flowgate.v02.0045 S1+S2 — Graph loader / Graph saver transport (D0004 §2.3 features/project):
// the Canvas read API and the layout save API of P0008 §3 / §4. Reuses the `@shared/api`
// transport like api.ts; snake_case stays inside this file's normalizers. The Designer reaches
// these only through the project store loaders (loadFlowCanvas / saveFlowCanvasLayout).
//
// Every Graph API request carries `x-canvas-client-id` (P0008 §1.1): one opaque id per browser
// tab (this module instance), so the SSE events this tab caused are told apart by tab, not by
// user (P0008 §8.1 / §8.3, flowCanvasEvents.ts).
//
// flowgate.v02.0064 T#4 (0064 P0006 §4.2 / §6.5): a node's card title state (`title_state`) and its title save.

import { extractApiErrorMessage, getRequest, putRequest } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import type {
  FlowCanvas,
  FlowCanvasCardDocument,
  FlowCanvasDocument,
  FlowCanvasEdge,
  FlowCanvasExternalRelation,
  FlowCanvasGraphBlock,
  FlowCanvasGraphNode,
  FlowCanvasLayoutInput,
  FlowCanvasLayoutResult,
  FlowCanvasNode,
  FlowCanvasNodeRun,
  FlowCanvasOwnership,
  FlowCanvasRun,
  FlowCanvasSlot,
  FlowCanvasTitleState,
  FlowCanvasView,
  FlowCanvasViewport,
  FlowGraphAssignee,
} from './types'

export type Raw = Record<string, any>

export const str = (value: unknown): string | null => (typeof value === 'string' ? value : null)
export const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

export function toViewport(raw: Raw | null | undefined): FlowCanvasViewport | null {
  if (!raw || typeof raw !== 'object') return null
  const x = num(raw.x)
  const y = num(raw.y)
  const zoom = num(raw.zoom)
  return x === null || y === null || zoom === null || zoom <= 0 ? null : { x, y, zoom }
}

function toDocument(raw: Raw | null | undefined): FlowCanvasDocument | null {
  if (!raw || typeof raw.doc_id !== 'string') return null
  return {
    docId: raw.doc_id,
    typeCode: str(raw.type_code),
    seq: num(raw.seq),
    title: str(raw.title),
    docReviewStatus: str(raw.doc_review_status),
    status: str(raw.status),
    supersedes: Array.isArray(raw.supersedes) ? raw.supersedes.filter((id: unknown) => typeof id === 'string') : [],
  }
}

function toSlot(raw: Raw | null | undefined): FlowCanvasSlot | null {
  if (!raw || typeof raw !== 'object') return null
  return {
    itemId: Number(raw.item_id),
    itemSeq: num(raw.item_seq),
    order: Number(raw.order) || 0,
    type: String(raw.type ?? ''),
    label: str(raw.label),
    derivedStatus: str(raw.derived_status),
    protected: raw.protected === true,
    sourceWpCardId: str(raw.source_wp_card_id),
  }
}

export function toAssignee(raw: Raw | null | undefined): FlowGraphAssignee | null {
  if (!raw || typeof raw.kind !== 'string') return null
  return { kind: raw.kind, userId: str(raw.user_id), providerId: str(raw.provider_id) }
}

function toGraphNode(raw: Raw | null | undefined): FlowCanvasGraphNode | null {
  if (!raw || typeof raw !== 'object') return null
  return {
    nodeType: String(raw.node_type ?? 'document'),
    label: str(raw.label) ?? '',
    paletteItemId: str(raw.palette_item_id),
    assignee: toAssignee(raw.assignee),
    rootBound: raw.root_bound === true,
  }
}

// flowgate.v02.0048 T#1: kept for the Inspector; the edge `condition` stays dropped (the top-level `run`: toRun).
// flowgate.v02.0056 F2-1: `awaiting_approval` (O1) and `stage` / `instruction_doc_id` (O2), kept only when sent.
// flowgate.v02.0061 T#14: the attempt's `integration_status` / `integration_reason`, `hold` and the attempt's running /
// requested execution counts, kept only when sent. T#15: the node's current `attempt_id` too (the Time Machine
// confirmation's basis), kept only when sent. flowgate.v02.0064 T#4: `node_running` (the card's running lock), kept only when sent.
function toNodeRun(raw: Raw | null | undefined): FlowCanvasNodeRun | null {
  if (!raw || typeof raw !== 'object') return null
  const running = count(raw.running_execution_count)
  const requested = count(raw.requested_execution_count)
  return {
    status: str(raw.status),
    openedAt: str(raw.opened_at),
    finishedAt: str(raw.finished_at),
    resultDocId: str(raw.result_doc_id),
    dispatchErrorCode: str(raw.dispatch?.error_code),
    ...(typeof raw.awaiting_approval === 'boolean' ? { awaitingApproval: raw.awaiting_approval } : {}),
    ...('stage' in raw ? { stage: str(raw.stage) } : {}),
    ...('instruction_doc_id' in raw ? { instructionDocId: str(raw.instruction_doc_id) } : {}),
    ...('integration_status' in raw ? { integrationStatus: str(raw.integration_status) } : {}),
    ...('integration_reason' in raw ? { integrationReason: str(raw.integration_reason) } : {}),
    ...(typeof raw.hold === 'boolean' ? { hold: raw.hold } : {}),
    ...(typeof raw.question === 'boolean' ? { question: raw.question } : {}),
    ...(typeof raw.node_running === 'boolean' ? { nodeRunning: raw.node_running } : {}),
    ...(running !== null ? { runningExecutionCount: running } : {}),
    ...(requested !== null ? { requestedExecutionCount: requested } : {}),
    ...('attempt_id' in raw ? { attemptId: str(raw.attempt_id) } : {}),
  }
}

/** A count the server sent as a whole number of zero or more; null otherwise. */
const count = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

// flowgate.v02.0053 T#5: the card's document lines (T#1 `card_documents`), in the server's order.
// flowgate.v02.0056 F2-2 (O3 0009-TR §4-1): `has_body` and the history counts (`review_count`, `rejection_count`,
// `history_count`), kept only when sent — a line from an older server says nothing about them.
export function toCardDocument(line: Raw): FlowCanvasCardDocument {
  const reviewCount = count(line.review_count)
  const rejectionCount = count(line.rejection_count)
  const historyCount = count(line.history_count)
  return {
    slot: String(line.slot),
    docType: str(line.doc_type),
    author: str(line.author),
    docId: str(line.doc_id),
    title: str(line.title),
    status: str(line.status),
    docReviewStatus: str(line.doc_review_status),
    ...(typeof line.has_body === 'boolean' ? { hasBody: line.has_body } : {}),
    ...(typeof line.body_state === 'string' || line.body_state === null ? { bodyState: line.body_state } : {}),
    ...(reviewCount !== null ? { reviewCount } : {}),
    ...(rejectionCount !== null ? { rejectionCount } : {}),
    ...(historyCount !== null ? { historyCount } : {}),
  }
}

export function toCardDocuments(raw: unknown): FlowCanvasCardDocument[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((line): line is Raw => !!line && typeof line === 'object' && typeof line.slot === 'string')
    .map(toCardDocument)
}

// flowgate.v02.0061 TR0031 rev1: the top-level `run` block — its status keeps the run lock while the run read is
// pending or failed. A block without a run id and status is no run. T#15: its `last_event_seq`, kept only when sent.
// flowgate.v02.0083 T#4 rev1: and the run band's facts of the same read, each kept only when sent.
function toRun(raw: Raw | null | undefined): FlowCanvasRun | null {
  if (!raw || typeof raw.run_id !== 'string' || typeof raw.status !== 'string') return null
  const lastEventSeq = count(raw.last_event_seq)
  return {
    runId: raw.run_id,
    status: raw.status,
    revisionNo: num(raw.revision_no),
    ...(lastEventSeq !== null ? { lastEventSeq } : {}),
    ...toRunBand(raw),
  }
}

function toRunBand(raw: Raw): Partial<FlowCanvasRun> {
  const running = count(raw.running_execution_count)
  const requested = count(raw.requested_execution_count)
  const pending = count(raw.pending_propagation_count)
  return {
    ...('mode' in raw ? { mode: str(raw.mode) } : {}),
    ...('idle_reason' in raw ? { idleReason: str(raw.idle_reason) } : {}),
    ...(running !== null ? { runningExecutionCount: running } : {}),
    ...(requested !== null ? { requestedExecutionCount: requested } : {}),
    ...('execution_limit' in raw ? { executionLimit: num(raw.execution_limit) } : {}),
    ...(pending !== null ? { pendingPropagationCount: pending } : {}),
    ...(Array.isArray(raw.pending_node_keys)
      ? { pendingNodeKeys: raw.pending_node_keys.filter((key: unknown): key is string => typeof key === 'string') }
      : {}),
  }
}

function toView(raw: Raw | null | undefined): FlowCanvasView | null {
  if (!raw || typeof raw.mode !== 'string') return null
  return { mode: raw.mode, revisionNo: num(raw.revision_no), draftVersion: num(raw.draft_version) }
}

function toGraphBlock(raw: Raw | null | undefined): FlowCanvasGraphBlock {
  const draft = raw?.draft
  const binding = raw?.root_binding
  return {
    draft: draft && typeof draft === 'object' && num(draft.draft_version) !== null
      ? {
          draftVersion: num(draft.draft_version)!,
          baseRevisionNo: num(draft.base_revision_no),
          updatedAt: str(draft.updated_at),
          updatedBy: str(draft.updated_by),
          confirmable: draft.confirmable === true,
          blockedReason: str(draft.blocked_reason),
        }
      : null,
    latestRevisionNo: num(raw?.latest_revision_no),
    rootBinding: binding && typeof binding.node_key === 'string'
      ? { nodeKey: binding.node_key, docId: str(binding.doc_id), boundAtRevisionNo: num(binding.bound_at_revision_no) }
      : null,
  }
}

function toNode(raw: Raw): FlowCanvasNode {
  const position = raw.position ?? {}
  const nodeRun = toNodeRun(raw.node_run)
  return {
    nodeKey: String(raw.node_key),
    identitySource: String(raw.identity_source ?? ''),
    origin: String(raw.origin ?? ''),
    kind: String(raw.kind ?? 'unknown'),
    docType: str(raw.doc_type),
    title: str(raw.title) ?? '',
    state: str(raw.state),
    position: { x: num(position.x) ?? 0, y: num(position.y) ?? 0, source: String(position.source ?? 'auto') },
    editable: { move: raw.editable?.move === true, structure: raw.editable?.structure === true },
    document: toDocument(raw.document),
    slot: toSlot(raw.slot),
    graphNode: toGraphNode(raw.graph_node),
    // Only a Graph node with a run carries it (the field stays absent elsewhere, as before 0048).
    ...(nodeRun ? { nodeRun } : {}),
    // Only a Graph / draft node carries its card lines (absent on document and sequence nodes).
    ...(Array.isArray(raw.card_documents) ? { cardDocuments: toCardDocuments(raw.card_documents) } : {}),
    // flowgate.v02.0064 T#4: kept only when sent (an older server sends none: no title edit, no version compare).
    ...('title_state' in raw ? { titleState: toTitleState(raw.title_state) } : {}),
  }
}

/** flowgate.v02.0064 T#4 (P0006 §4.2.1): `title_state` -> one bundle, or null (not a title node, or malformed). */
export function toTitleState(raw: unknown): FlowCanvasTitleState | null {
  const value = raw as Raw | null
  const version = count(value?.version)
  if (!value || typeof value !== 'object' || version === null) return null
  return { title: str(value.title), titleSource: str(value.title_source), version }
}

function toEdge(raw: Raw): FlowCanvasEdge {
  return {
    edgeId: String(raw.edge_id),
    edgeClass: String(raw.edge_class ?? 'execution'),
    origin: String(raw.origin ?? ''),
    source: String(raw.source),
    target: String(raw.target),
    relation: str(raw.relation),
    editable: raw.editable === true,
  }
}

function toExternal(raw: Raw): FlowCanvasExternalRelation {
  return {
    nodeKey: String(raw.node_key),
    relation: String(raw.relation ?? ''),
    otherDocId: String(raw.other_doc_id ?? ''),
    otherFlowId: str(raw.other_flow_id),
    otherTitle: str(raw.other_title),
  }
}

export function toOwnership(raw: Raw | null | undefined): FlowCanvasOwnership {
  return {
    state: String(raw?.state ?? 'unassigned'),
    sequenceRootDocId: str(raw?.sequence_root_doc_id),
    graphRevisionNo: num(raw?.graph_revision_no),
    assignedAt: str(raw?.assigned_at),
  }
}

export function toFlowCanvas(raw: Raw): FlowCanvas {
  const ownership = raw.ownership ?? {}
  const identity = raw.identity ?? {}
  const collapsed = raw.collapsed ?? {}
  return {
    flowId: String(raw.flow_id),
    projectId: str(raw.project_id),
    ownership: toOwnership(ownership),
    projection: String(raw.projection ?? ''),
    nodes: Array.isArray(raw.nodes) ? raw.nodes.map(toNode) : [],
    edges: Array.isArray(raw.edges) ? raw.edges.map(toEdge) : [],
    externalRelations: Array.isArray(raw.external_relations) ? raw.external_relations.map(toExternal) : [],
    viewport: toViewport(raw.viewport),
    identity: {
      sequenceTag: str(identity.sequence_tag),
      rematched: identity.rematched === true,
      retiredNodeKeys: Array.isArray(identity.retired_node_keys) ? identity.retired_node_keys.map(String) : [],
    },
    collapsed: {
      applied: Array.isArray(collapsed.applied) ? collapsed.applied.map(String) : [],
      ignoredKeys: Array.isArray(collapsed.ignored_keys) ? collapsed.ignored_keys.map(String) : [],
      hiddenCounts: collapsed.hidden_counts && typeof collapsed.hidden_counts === 'object' ? { ...collapsed.hidden_counts } : {},
    },
    view: toView(raw.view),
    graph: toGraphBlock(raw.graph),
    // Absent from an older server's answer: the Canvas then says nothing about the run.
    ...('run' in raw ? { run: toRun(raw.run) } : {}),
  }
}

// Same shape as api.ts: a displayable message, with the transport error kept as `cause`.
// flowgate.v02.0105 T#1: and its HTTP status / network failure (`status` / `network`), so the Designer tells a
// refused Flow (403 / 404) apart from a passing failure.
export function wrapError(error: unknown, fallback: string): Error {
  const { status, network } = normalizeApiError(error)
  const wrapped = new Error(extractApiErrorMessage(error, fallback)) as Error & { cause?: unknown; status: number | null; network: boolean }
  wrapped.cause = error
  wrapped.status = status
  wrapped.network = network
  return wrapped
}

const canvasPath = (flowId: string) => `/api/v1/flows/${encodeURIComponent(flowId)}/canvas`

function newCanvasClientId(): string {
  const bytes = new Uint8Array(9)
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes)
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  return `tab_${[...bytes].map((b) => b.toString(36).padStart(2, '0')).join('')}`
}

/** This tab's `x-canvas-client-id` (P0008 §1.1: 1~64 of [A-Za-z0-9_-], kept while the tab lives). */
export const CANVAS_CLIENT_ID = newCanvasClientId()
export const canvasHeaders = () => ({ headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } })

/**
 * GET /api/v1/flows/{flowId}/canvas — the Flow's Canvas graph. `view` (`draft` | `revision`)
 * picks the Graph definition of a Graph Flow; omitted, the server picks (P0008 §3). `collapse`:
 * the collapse groups (P0008 §2.5, e.g. `conversation`) whose free documents the server leaves out.
 */
export async function fetchFlowCanvas(flowId: string, view?: string, collapse: readonly string[] = []): Promise<FlowCanvas> {
  const params: Record<string, string> = {}
  if (view) params.view = view
  if (collapse.length) params.collapse = collapse.join(',')
  try {
    const response = await getRequest<Raw>(canvasPath(flowId), params, canvasHeaders())
    return toFlowCanvas(response.data ?? {})
  } catch (error) {
    throw wrapError(error, 'Failed to load the flow canvas.')
  }
}

/** flowgate.v02.0064 T#4: a refused title save — the server's `error.code` / `params` (P0006 §4.2.3), its sentence. */
export class FlowNodeTitleError extends Error {
  readonly code: string | null
  readonly params: Record<string, unknown>
  readonly status: number | null

  constructor(message: string, code: string | null, params: Record<string, unknown>, status: number | null) {
    super(message)
    this.name = 'FlowNodeTitleError'
    this.code = code
    this.params = params
    this.status = status
  }
}

/**
 * flowgate.v02.0064 T#4 (P0006 §4.2.2): PUT /api/v1/flows/{flowId}/graph/nodes/{nodeKey}/title — the title only (no
 * version: the server never refuses on one). Resolves the saved `title_state`; a refusal rejects with FlowNodeTitleError.
 */
export async function nodeTitleSave(flowId: string, nodeKey: string, title: string): Promise<FlowCanvasTitleState> {
  const path = `/api/v1/flows/${encodeURIComponent(flowId)}/graph/nodes/${encodeURIComponent(nodeKey)}/title`
  let data: Raw
  try {
    data = (await putRequest<Raw>(path, { title }, canvasHeaders())).data ?? {}
  } catch (error) {
    const response = (error as { response?: { status?: number; data?: Raw } } | null)?.response
    const envelope = response?.data?.error
    const message = str(envelope?.message) || wrapError(error, 'Failed to save the card title.').message
    const params = envelope?.params && typeof envelope.params === 'object' ? { ...envelope.params } : {}
    const refusal = new FlowNodeTitleError(message, str(envelope?.code), params, num(response?.status))
    ;(refusal as Error & { cause?: unknown }).cause = error
    throw refusal
  }
  const state = toTitleState(data.title_state)
  if (!state) throw new FlowNodeTitleError('Failed to save the card title.', null, {}, null)
  return state
}

/** PUT /api/v1/flows/{flowId}/canvas/layout — moved node positions and / or the viewport. */
export async function saveFlowCanvasLayout(flowId: string, input: FlowCanvasLayoutInput): Promise<FlowCanvasLayoutResult> {
  try {
    const response = await putRequest<Raw>(`${canvasPath(flowId)}/layout`, {
      positions: input.positions.map((p) => ({ node_key: p.nodeKey, x: p.x, y: p.y })),
      viewport: input.viewport ? { x: input.viewport.x, y: input.viewport.y, zoom: input.viewport.zoom } : null,
    }, canvasHeaders())
    const data = response.data ?? {}
    return {
      savedPositions: Number(data.saved_positions) || 0,
      viewportSaved: data.viewport_saved === true,
      ignored: Array.isArray(data.ignored)
        ? data.ignored.map((item: Raw) => ({ nodeKey: String(item.node_key), reason: String(item.reason ?? '') }))
        : [],
    }
  } catch (error) {
    throw wrapError(error, 'Failed to save the flow canvas layout.')
  }
}
