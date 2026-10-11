// flowgate.v02.0045 S1+S2 — Graph loader / Graph saver transport (D0004 §2.3 features/project):
// the Canvas read API and the layout save API of P0008 §3 / §4. Reuses the `@shared/api`
// transport like api.ts; snake_case stays inside this file's normalizers. The Designer reaches
// these only through the project store loaders (loadFlowCanvas / saveFlowCanvasLayout).
//
// Every Graph API request carries `x-canvas-client-id` (P0008 §1.1): one opaque id per browser
// tab (this module instance), so the SSE events this tab caused are told apart by tab, not by
// user (P0008 §8.1 / §8.3, flowCanvasEvents.ts).

import { extractApiErrorMessage, getRequest, putRequest } from '@shared/api'
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
  FlowCanvasSlot,
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

// flowgate.v02.0048 T#1: kept for the Inspector; the top-level `run` and edge `condition` stay dropped.
function toNodeRun(raw: Raw | null | undefined): FlowCanvasNodeRun | null {
  if (!raw || typeof raw !== 'object') return null
  return { status: str(raw.status), openedAt: str(raw.opened_at), finishedAt: str(raw.finished_at), resultDocId: str(raw.result_doc_id) }
}

// flowgate.v02.0053 T#5: the card's document lines (T#1 `card_documents`), in the server's order.
export function toCardDocuments(raw: unknown): FlowCanvasCardDocument[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((line): line is Raw => !!line && typeof line === 'object' && typeof line.slot === 'string')
    .map((line) => ({
      slot: line.slot,
      docType: str(line.doc_type),
      author: str(line.author),
      docId: str(line.doc_id),
      title: str(line.title),
      status: str(line.status),
      docReviewStatus: str(line.doc_review_status),
    }))
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
  }
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
  }
}

// Same shape as api.ts: a displayable message, with the transport error kept as `cause`.
export function wrapError(error: unknown, fallback: string): Error {
  const wrapped = new Error(extractApiErrorMessage(error, fallback)) as Error & { cause?: unknown }
  wrapped.cause = error
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
