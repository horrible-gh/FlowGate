// flowgate.v02.0026 — FlowGate Graph Model (0003-D §3.2.1). The one Graph shape the Center
// session keeps per Flow. It knows nothing about the renderer: Vue Flow node/edge objects are
// derived from it one way (canvas/vueFlowMapping.ts) and never written back.
//
// kind / state are open domain keys, not display text — canvas/flowNodeKinds.ts maps the
// known ones and shows anything else with the default look. Node size, per-Node detail data,
// Edge kind / condition and Flow metadata are deliberately absent until the server Graph
// contract is designed (0003-D DEFERRED); they will be added as optional fields.

export interface FlowGraphNode {
  /** Unique within the Graph. */
  readonly id: string
  /** Domain key, e.g. 'requirement' / 'task'. Unknown keys render with the default look. */
  readonly kind: string
  readonly title: string
  /** Optional domain key, display only. */
  readonly state?: string | null
  /** Free World coordinates — never derived from an order / sequence. */
  readonly x: number
  readonly y: number
}

export interface FlowGraphEdge {
  /** Unique within the Graph. */
  readonly id: string
  /** Source Node id. */
  readonly source: string
  /** Target Node id. */
  readonly target: string
}

export interface FlowGraph {
  /** Same value as FlowRef.groupId of the Flow this Graph belongs to. */
  readonly flowId: string
  readonly nodes: readonly FlowGraphNode[]
  readonly edges: readonly FlowGraphEdge[]
}
