// which Edges the work has already gone through, from the actual state
// of their two Nodes (the server's document review / node_run state, P0008 §2.3). Only an
// execution Edge can be progressed: it is progressed when its source Node is done and its target
// Node has been reached (it shows any state past `draft`). A reference Edge (document relation
// such as triggered_by / target) or an Edge without a class has no execution meaning, so it is
// never progressed whatever its two Nodes show. A progressed Edge is drawn apart from one not yet
// taken and can never be removed. Pure: no Vue, no transport.
//
// flowgate.v02.0045 TR0020 rev5: an execution Edge leaving a 요구사항 Node is progressed as soon
// as its target has been reached. The 요구사항 is the workflow root: its document stays
// `wf_in_progress` (Canvas `in_progress`) until the whole workflow ends, so "source done" never
// held for it while the work had already gone past it.
import type { FlowGraph, FlowGraphEdge } from './flowGraph'

/** Node states that mean "the work has arrived here" (P0008 §2.3 Canvas states, `draft` excluded). */
const REACHED_STATES: ReadonlySet<string> = new Set(['in_progress', 'review', 'done', 'blocked'])

/** Ids of the progressed Edges of `graph`. */
export function progressedEdgeIds(graph: FlowGraph): Set<string> {
  const states = new Map(graph.nodes.map((node) => [node.id, node.state ?? null] as const))
  const requirements = new Set(graph.nodes.filter((node) => node.kind === 'requirement').map((node) => node.id))
  const out = new Set<string>()
  for (const edge of graph.edges) {
    if (isProgressed(edge, states, requirements)) out.add(edge.id)
  }
  return out
}

export function isEdgeProgressed(graph: FlowGraph, edgeId: string): boolean {
  return progressedEdgeIds(graph).has(edgeId)
}

function isProgressed(
  edge: FlowGraphEdge,
  states: ReadonlyMap<string, string | null>,
  requirements: ReadonlySet<string>,
): boolean {
  if (edge.edgeClass !== 'execution') return false
  const target = states.get(edge.target)
  if (!target || !REACHED_STATES.has(target)) return false
  return requirements.has(edge.source) || states.get(edge.source) === 'done'
}

/** Display only: a completed input has arrived at a join still waiting for a known peer.
 * Missing states and references are not evidence. Requirement roots are already satisfied
 * once execution passes them (the same exception as progressed edges above).
 * This set deliberately has no effect on deletion protection or execution scheduling.
 */
export function waitingEdgeIds(graph: FlowGraph): Set<string> {
  const nodes = new Map(graph.nodes.map(node => [node.id, node]))
  const inputs = new Map<string, FlowGraphEdge[]>()
  for (const edge of graph.edges) {
    if (edge.edgeClass !== 'execution' || edge.source === edge.target) continue
    const incoming = inputs.get(edge.target) ?? []
    incoming.push(edge)
    inputs.set(edge.target, incoming)
  }
  const unfinished = new Set(['draft', 'in_progress', 'review', 'blocked'])
  const result = new Set<string>()
  for (const [targetId, incoming] of inputs) {
    if (nodes.get(targetId)?.state !== 'draft') continue
    for (const edge of incoming) {
      if (nodes.get(edge.source)?.state !== 'done') continue
      if (incoming.some(peer => {
        const source = nodes.get(peer.source)
        return peer.source !== edge.source && source?.kind !== 'requirement'
          && source?.state != null && unfinished.has(source.state)
      })) result.add(edge.id)
    }
  }
  return result
}
