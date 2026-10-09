// flowgate.v02.0045 — a server Canvas read (P0008 §3, S1 shape) for Shell-level tests whose
// `@shared/api` getRequest mock now also receives GET /api/v1/flows/{flowId}/canvas from the
// Designer's server Graph source. Two document Nodes joined by one reference Edge.

const CANVAS_URL = /^\/api\/v1\/flows\/([^/]+)\/canvas$/

export function flowCanvasResponse(flowId: string, ownership = 'unassigned') {
  const node = (seq: number, type: string, kind: string, title: string) => ({
    node_key: `doc:${flowId}.000${seq}-${type}`,
    identity_source: 'document',
    origin: 'document',
    kind,
    doc_type: type,
    title,
    state: 'done',
    position: { x: 260 * (seq - 1), y: 0, source: 'auto' },
    editable: { move: true, structure: false },
    document: null,
    slot: null,
    graph_node: null,
    node_run: null,
  })
  return {
    ok: true,
    server_ts: '2026-10-08T05:30:12.481+00:00',
    flow_id: flowId,
    project_id: 'p',
    ownership: { state: ownership, sequence_root_doc_id: null, graph_revision_no: null, assigned_at: null },
    projection: 'documents',
    view: null,
    graph: { draft: null, latest_revision_no: null, root_binding: null },
    nodes: [node(1, 'R', 'requirement', '요구사항'), node(2, 'N', 'research', '조사지시')],
    edges: [{
      edge_id: `ref:triggered_by:${flowId}.0001-R>${flowId}.0002-N`,
      edge_class: 'reference',
      origin: 'document_relation',
      source: `doc:${flowId}.0001-R`,
      target: `doc:${flowId}.0002-N`,
      relation: 'triggered_by',
      condition: null,
      editable: false,
    }],
    external_relations: [],
    viewport: null,
    run: null,
    identity: { sequence_tag: null, rematched: false, retired_node_keys: [] },
    collapsed: { applied: [], ignored_keys: [], hidden_counts: {} },
  }
}

/** The mocked `getRequest` answer for a Canvas read URL, or null for any other URL. */
export function answerFlowCanvas(url: string): { data: unknown } | null {
  const match = CANVAS_URL.exec(url)
  return match ? { data: flowCanvasResponse(decodeURIComponent(match[1])) } : null
}
