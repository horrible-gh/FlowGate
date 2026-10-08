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
  FlowCanvasDocument,
  FlowCanvasEdge,
  FlowCanvasExternalRelation,
  FlowCanvasLayoutInput,
  FlowCanvasLayoutResult,
  FlowCanvasNode,
  FlowCanvasSlot,
  FlowCanvasViewport,
} from './types'

type Raw = Record<string, any>

const str = (value: unknown): string | null => (typeof value === 'string' ? value : null)
const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

function toViewport(raw: Raw | null | undefined): FlowCanvasViewport | null {
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

function toNode(raw: Raw): FlowCanvasNode {
  const position = raw.position ?? {}
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

export function toFlowCanvas(raw: Raw): FlowCanvas {
  const ownership = raw.ownership ?? {}
  const identity = raw.identity ?? {}
  const collapsed = raw.collapsed ?? {}
  return {
    flowId: String(raw.flow_id),
    projectId: str(raw.project_id),
    ownership: {
      state: String(ownership.state ?? 'unassigned'),
      sequenceRootDocId: str(ownership.sequence_root_doc_id),
      graphRevisionNo: num(ownership.graph_revision_no),
      assignedAt: str(ownership.assigned_at),
    },
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
  }
}

// Same shape as api.ts: a displayable message, with the transport error kept as `cause`.
function wrapError(error: unknown, fallback: string): Error {
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
const canvasHeaders = () => ({ headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } })

/** GET /api/v1/flows/{flowId}/canvas — the Flow's Canvas graph (S1 + S2 positions / viewport). */
export async function fetchFlowCanvas(flowId: string): Promise<FlowCanvas> {
  try {
    const response = await getRequest<Raw>(canvasPath(flowId), {}, canvasHeaders())
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
