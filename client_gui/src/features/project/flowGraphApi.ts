// flowgate.v02.0045 S3+S4 — Graph saver transport (D0004 §2.3 features/project): the Graph
// definition API of P0008 §5 / §6 — read the definition, save the draft (whole replacement),
// validate it and confirm it. snake_case stays inside this file; every request carries this
// tab's `x-canvas-client-id` (flowCanvasApi.ts) so the SSE events it causes are told apart.
//
// A refused request rejects with a FlowGraphApiError: `code` / `params` are the server's
// `error.code` / `error.params` (P0008 §1.3), `message` is safe to show. The Designer branches on
// `code` only (draft_version_conflict, graph_validation_failed, flow_owned_by_v01, ...).

import { getRequest, postRequest, putRequest } from '@shared/api'
import { canvasHeaders, num, str, toAssignee, toCardDocuments, toOwnership, wrapError, type Raw } from './flowCanvasApi'
import type {
  FlowGraphConfirmResult,
  FlowGraphDefinition,
  FlowGraphDefinitionEdge,
  FlowGraphDefinitionNode,
  FlowGraphDraftInput,
  FlowGraphDraftSaveResult,
  FlowGraphIssue,
  FlowGraphValidation,
} from './types'

export class FlowGraphApiError extends Error {
  readonly code: string | null
  readonly params: Record<string, unknown>
  readonly status: number | null
  /** The structure / root binding issues of a `graph_validation_failed` refusal, else []. */
  readonly issues: FlowGraphIssue[]

  constructor(message: string, code: string | null, params: Record<string, unknown>, status: number | null, cause: unknown) {
    super(message)
    this.name = 'FlowGraphApiError'
    this.code = code
    this.params = params
    this.status = status
    const raw = code === 'graph_validation_failed' ? params.issues : null
    this.issues = Array.isArray(raw) ? raw.filter((i) => i && typeof i === 'object').map((i) => toIssue(i as Raw)) : []
    ;(this as Error & { cause?: unknown }).cause = cause
  }
}

/** A refused request -> FlowGraphApiError (the server's envelope code / params). Also the run transport's (flowRunApi.ts). */
export function toGraphError(error: unknown, fallback: string): FlowGraphApiError {
  const response = (error as { response?: { status?: number; data?: Raw } } | null)?.response
  const envelope = response?.data?.error
  const code = envelope && typeof envelope.code === 'string' ? envelope.code : null
  const params = envelope && envelope.params && typeof envelope.params === 'object' ? { ...envelope.params } : {}
  const message = envelope && typeof envelope.message === 'string' && envelope.message
    ? envelope.message
    : wrapError(error, fallback).message
  return new FlowGraphApiError(message, code, params, typeof response?.status === 'number' ? response.status : null, error)
}

const graphPath = (flowId: string) => `/api/v1/flows/${encodeURIComponent(flowId)}/graph`

const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [])

export function toIssue(raw: Raw): FlowGraphIssue {
  return {
    code: String(raw.code ?? ''),
    ruleRef: str(raw.rule_ref),
    structure: str(raw.structure),
    nodeKeys: strings(raw.node_keys),
    edgeIds: strings(raw.edge_ids),
    message: str(raw.message) ?? '',
  }
}

function toDefinitionNode(raw: Raw): FlowGraphDefinitionNode {
  return {
    nodeKey: String(raw.node_key),
    nodeType: String(raw.node_type ?? 'document'),
    docType: str(raw.doc_type),
    paletteItemId: str(raw.palette_item_id),
    label: str(raw.label) ?? '',
    assignee: toAssignee(raw.assignee),
    // flowgate.v02.0061 T#15: every node of the draft and the revision says whether its card is held (T#11).
    ...(typeof raw.hold === 'boolean' ? { hold: raw.hold } : {}),
  }
}

function toDefinitionEdge(raw: Raw): FlowGraphDefinitionEdge {
  return { edgeId: String(raw.edge_id), source: String(raw.source), target: String(raw.target) }
}

function toDefinitionPart(raw: Raw | null | undefined) {
  return {
    nodes: Array.isArray(raw?.nodes) ? raw!.nodes.map(toDefinitionNode) : [],
    edges: Array.isArray(raw?.edges) ? raw!.edges.map(toDefinitionEdge) : [],
  }
}

export function toFlowGraphDefinition(raw: Raw): FlowGraphDefinition {
  const draft = raw.draft && typeof raw.draft === 'object' ? raw.draft : null
  const latest = raw.latest_revision && typeof raw.latest_revision === 'object' ? raw.latest_revision : null
  return {
    flowId: String(raw.flow_id),
    ownership: toOwnership(raw.ownership),
    draft: draft ? { draftVersion: num(draft.draft_version) ?? 0, ...toDefinitionPart(draft) } : null,
    latestRevision: latest ? { revisionNo: num(latest.revision_no) ?? 0, ...toDefinitionPart(latest) } : null,
    policies: raw.policies && typeof raw.policies === 'object' ? { ...raw.policies } : {},
  }
}

/** GET /api/v1/flows/{flowId}/graph — draft, latest revision and policy slots. */
export async function fetchFlowGraph(flowId: string): Promise<FlowGraphDefinition> {
  try {
    const response = await getRequest<Raw>(graphPath(flowId), {}, canvasHeaders())
    return toFlowGraphDefinition(response.data ?? {})
  } catch (error) {
    throw toGraphError(error, 'Failed to load the flow graph.')
  }
}

/** PUT /api/v1/flows/{flowId}/graph/draft — replaces the whole draft (P0008 S3-1). */
export async function saveFlowGraphDraft(flowId: string, input: FlowGraphDraftInput): Promise<FlowGraphDraftSaveResult> {
  try {
    const response = await putRequest<Raw>(`${graphPath(flowId)}/draft`, {
      draft_version: input.draftVersion,
      nodes: input.nodes.map((n) => ({
        node_key: n.nodeKey,
        client_key: n.clientKey,
        node_type: n.nodeType,
        doc_type: n.docType,
        palette_item_id: n.paletteItemId,
        label: n.label,
        assignee: n.assignee ? { kind: n.assignee.kind, user_id: n.assignee.userId, provider_id: n.assignee.providerId } : null,
      })),
      edges: input.edges.map((e) => ({
        edge_id: e.edgeId, client_key: e.clientKey, source: e.source, target: e.target, condition: null,
      })),
    }, canvasHeaders())
    const data = response.data ?? {}
    const keyMap: Record<string, string> = {}
    for (const [k, v] of Object.entries(data.key_map ?? {})) if (typeof v === 'string') keyMap[k] = v
    // flowgate.v02.0053 T#5: the saved draft nodes' card lines (T#1), so a placed card shows them at once.
    const lines = data.card_documents
    const cardDocuments = lines && typeof lines === 'object' && !Array.isArray(lines)
      ? Object.fromEntries(Object.entries(lines as Raw).map(([key, value]) => [key, toCardDocuments(value)]))
      : null
    return {
      draftVersion: num(data.draft_version) ?? input.draftVersion + 1,
      keyMap,
      removedNodeKeys: strings(data.removed_node_keys),
      removedEdgeIds: strings(data.removed_edge_ids),
      ...(cardDocuments ? { cardDocuments } : {}),
    }
  } catch (error) {
    throw toGraphError(error, 'Failed to save the flow graph draft.')
  }
}

/** POST /api/v1/flows/{flowId}/graph/draft/validate — checks only, writes nothing (S3-5). */
export async function validateFlowGraphDraft(flowId: string, draftVersion: number): Promise<FlowGraphValidation> {
  try {
    const response = await postRequest<Raw>(`${graphPath(flowId)}/draft/validate`, { draft_version: draftVersion }, canvasHeaders())
    const data = response.data ?? {}
    const preview = data.root_binding_preview
    return {
      draftVersion: num(data.draft_version) ?? draftVersion,
      confirmable: data.confirmable === true,
      issues: Array.isArray(data.issues) ? data.issues.map(toIssue) : [],
      rootBindingPreview: preview && typeof preview.status === 'string'
        ? { status: preview.status, nodeKey: str(preview.node_key), docId: str(preview.doc_id), reason: str(preview.reason) }
        : null,
    }
  } catch (error) {
    throw toGraphError(error, 'Failed to validate the flow graph draft.')
  }
}

/** POST /api/v1/flows/{flowId}/graph/revisions — confirms the draft (S4-1~S4-7). */
export async function confirmFlowGraphRevision(
  flowId: string,
  draftVersion: number,
  baseRevisionNo: number | null,
): Promise<FlowGraphConfirmResult> {
  try {
    const response = await postRequest<Raw>(`${graphPath(flowId)}/revisions`, {
      draft_version: draftVersion,
      base_revision_no: baseRevisionNo,
    }, canvasHeaders())
    const data = response.data ?? {}
    const binding = data.root_binding ?? {}
    return {
      revisionNo: num(data.revision_no) ?? 0,
      ownership: toOwnership(data.ownership),
      rootBinding: {
        status: String(binding.status ?? 'not_needed'),
        nodeKey: str(binding.node_key),
        docId: str(binding.doc_id),
        boundAtRevisionNo: num(binding.bound_at_revision_no),
      },
      retiredNodeKeys: strings(data.retired_node_keys),
    }
  } catch (error) {
    throw toGraphError(error, 'Failed to confirm the flow graph.')
  }
}
