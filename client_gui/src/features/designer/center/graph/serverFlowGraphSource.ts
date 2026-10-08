// flowgate.v02.0045 S1+S2 — server Graph source (D0004 §2.3 "서버 Graph 공급원"): implements the
// FlowGraphSource boundary over the Canvas read / layout save API of P0008 §3 / §4. It replaces
// the sample source in the Designer (isSample = false, no sample badge). The transport model
// comes from the features/project store loaders, so this file holds no API, store or axios of
// its own; it only translates FlowCanvas <-> FlowGate Graph Model.
import type { FlowCanvas, FlowCanvasChangeHandlers, FlowCanvasLayoutInput, FlowCanvasLayoutResult } from '@/features/project'
import type { FlowGraph } from './flowGraph'
import type { FlowGraphSource, FlowLayoutChange, FlowLayoutSaveResult } from './flowGraphSource'

/** The project store loaders this source needs (`useProjectStore()` satisfies it). */
export interface FlowCanvasLoaders {
  loadFlowCanvas(flowId: string): Promise<FlowCanvas>
  saveFlowCanvasLayout(flowId: string, input: FlowCanvasLayoutInput): Promise<FlowCanvasLayoutResult>
  /** Other tabs' / devices' Canvas changes (P0008 §8); absent = no change reports. */
  subscribeFlowCanvasChanges?(projectId: string, handlers: FlowCanvasChangeHandlers): () => void
}

/** Server Canvas -> FlowGate Graph Model. Node ids are the server node identities. */
export function toFlowGraph(canvas: FlowCanvas): FlowGraph {
  return {
    flowId: canvas.flowId,
    nodes: canvas.nodes.map((node) => ({
      id: node.nodeKey,
      kind: node.kind,
      title: node.title,
      state: node.state,
      x: node.position.x,
      y: node.position.y,
      positionSource: node.position.source,
      movable: node.editable.move,
    })),
    edges: canvas.edges.map((edge) => ({
      id: edge.edgeId,
      source: edge.source,
      target: edge.target,
      edgeClass: edge.edgeClass,
    })),
    ownership: canvas.ownership.state,
    viewport: canvas.viewport,
  }
}

export function toLayoutInput(change: FlowLayoutChange): FlowCanvasLayoutInput {
  return {
    positions: change.positions.map((p) => ({ nodeKey: p.nodeId, x: p.x, y: p.y })),
    viewport: change.viewport ? { x: change.viewport.x, y: change.viewport.y, zoom: change.viewport.zoom } : null,
  }
}

export function createServerFlowGraphSource(loaders: FlowCanvasLoaders): FlowGraphSource {
  return {
    isSample: false,
    // The Flow id is the global v0.1 group id; the project id is not part of the address.
    loadGraph: async (_projectId: string, flowId: string) => toFlowGraph(await loaders.loadFlowCanvas(flowId)),
    saveLayout: async (_projectId: string, flowId: string, change: FlowLayoutChange): Promise<FlowLayoutSaveResult> => {
      const result = await loaders.saveFlowCanvasLayout(flowId, toLayoutInput(change))
      return { ignoredNodeIds: result.ignored.map((item) => item.nodeKey) }
    },
    watchChanges: loaders.subscribeFlowCanvasChanges
      ? (projectId, handlers) => loaders.subscribeFlowCanvasChanges!(projectId, handlers)
      : undefined,
  }
}
