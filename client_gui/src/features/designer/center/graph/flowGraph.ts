// flowgate.v02.0026 — FlowGate Graph Model (0003-D §3.2.1). The one Graph shape the Center
// session keeps per Flow. It knows nothing about the renderer: Vue Flow node/edge objects are
// derived from it one way (canvas/vueFlowMapping.ts) and never written back.
//
// kind / state are open domain keys, not display text — canvas/flowNodeKinds.ts maps the
// known ones and shows anything else with the default look. Node size, per-Node detail data,
// Edge condition and Flow metadata are deliberately absent until the server Graph contract
// needs them (0003-D DEFERRED); they are added as optional fields.
//
// flowgate.v02.0045 S1+S2 (D0004 §2.3 "Graph 모델 확장"): optional meaning fields (edge class,
// ownership) and layout fields (position source, movable, saved viewport) from the server
// Canvas read. A source that does not know them (the sample source) simply leaves them out.

export interface FlowGraphNode {
  /** Unique within the Graph — the server node identity for a server Graph. */
  readonly id: string
  /** Domain key, e.g. 'requirement' / 'task'. Unknown keys render with the default look. */
  readonly kind: string
  readonly title: string
  /** Optional domain key, display only. */
  readonly state?: string | null
  /** Free World coordinates — never derived from an order / sequence. */
  readonly x: number
  readonly y: number
  /** Layout: 'saved' = stored position, 'auto' = server auto placement. */
  readonly positionSource?: string | null
  /** Layout: true when the user may drag the Node (its position is then saved). */
  readonly movable?: boolean
}

export interface FlowGraphEdge {
  /** Unique within the Graph. */
  readonly id: string
  /** Source Node id. */
  readonly source: string
  /** Target Node id. */
  readonly target: string
  /** Meaning: 'execution' (decides what runs next) or 'reference' (document relation only). */
  readonly edgeClass?: string | null
}

export interface FlowGraphViewport {
  readonly x: number
  readonly y: number
  readonly zoom: number
}

export interface FlowGraph {
  /** Same value as FlowRef.groupId of the Flow this Graph belongs to. */
  readonly flowId: string
  readonly nodes: readonly FlowGraphNode[]
  readonly edges: readonly FlowGraphEdge[]
  /** Which engine owns the Flow: 'unassigned' | 'v01' | 'graph' | 'conflict' (open key). */
  readonly ownership?: string | null
  /** Layout: the user's saved Viewport of this Flow, or null (fit all Nodes). */
  readonly viewport?: FlowGraphViewport | null
}
