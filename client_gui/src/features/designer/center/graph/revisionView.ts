import type { FlowGraph } from './flowGraph'

/** A revision view with both numbers known that is different from the latest definition. */
export function isNonLatestRevision(graph: FlowGraph): boolean {
  return graph.viewMode === 'revision'
    && graph.viewRevisionNo != null
    && graph.latestRevisionNo != null
    && graph.viewRevisionNo !== graph.latestRevisionNo
}
