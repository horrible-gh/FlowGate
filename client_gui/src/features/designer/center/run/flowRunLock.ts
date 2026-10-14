// flowgate.v02.0061 T#14 — what a Flow's run means for editing and for the Canvas lines, by the server's rules
// (flowgate.graph_run.editing, T#3). Pure: no Vue, no transport.
//
// - Run lock: a run that is running, paused or stopping locks every structure edit of its Flow (Canvas: nodes and
//   lines added / removed / changed; Inspector: card settings). Viewing, panning, zooming and moving cards stay.
// - Protected nodes: a node that is done, open or review — and every execution line into it — never changes, whatever
//   the run's status (also when it is idle). The server judges it by the run's node states; the screen reads them from
//   the Canvas node runs and from the run read, so a draft view (no node runs on its nodes) is protected the same.
// - Running lines: a line into a node the work is going through now — open or review, or one with a running /
//   requested AI execution or a pending / integrating result integration — is drawn as running (FlowEdgePath).
// - Run status not confirmed (TR0031 rev1): until a read tells the Flow's run status (the run read, or the Canvas read's
//   `run` block) the structure waits too (`unknown`) — a run being read or whose read failed is never taken as idle. A
//   run already known to be locked stays locked whatever its Graph's load does (no Graph: no protected / running nodes).
// - Card marks (TR0031 rev1): a card shows its attempt's result integration and Hold from the run read's node runs as
//   well (`nodeRuns`, the latest), so a draft view — whose nodes carry no node run — keeps them. A node is the run's
//   node only by its node key: a node added to the draft has a key of its own (the server never reuses a known or
//   retired key; an unsaved one is a `tmp_` client key), so it gets nothing.
import type { FlowGraph, FlowGraphEdge, FlowGraphNodeRun } from '../graph/flowGraph'
import type { FlowRunInfo, FlowRunNodeInfo } from '../graph/flowGraphSource'

/** Run statuses that lock editing (server `editing.LOCKED`). */
export const RUN_LOCKED_STATUSES: readonly string[] = ['running', 'paused', 'stopping']

/** Node states that are always protected (server `editing.PROTECTED`). */
export const PROTECTED_NODE_STATES: readonly string[] = ['done', 'open', 'review']

/** Node states the work is going through now. */
const ACTIVE_NODE_STATES: readonly string[] = ['open', 'review']

/** Result integrations still going on (server `result_merge.IN_PROGRESS`). */
const ACTIVE_INTEGRATIONS: readonly string[] = ['pending', 'integrating']

/**
 * Why a Flow's structure cannot be edited now: its run is locked (`run`), its run status is not confirmed yet (`unknown`:
 * being read, or the read failed), or null.
 */
export type FlowEditLock = 'run' | 'unknown' | null

export function isRunLocked(run: Pick<FlowRunInfo, 'status'> | null | undefined): boolean {
  return !!run && RUN_LOCKED_STATUSES.includes(run.status)
}

/** `known`: the run status is confirmed (`run` null = no run); a locked run locks even when it is not. */
export function editLockOf(run: Pick<FlowRunInfo, 'status'> | null | undefined, known = true): FlowEditLock {
  if (isRunLocked(run)) return 'run'
  return known ? null : 'unknown'
}

type NodeRunFacts = Pick<FlowRunNodeInfo, 'status' | 'integrationStatus' | 'runningExecutionCount' | 'requestedExecutionCount'>

/** The run read's node runs of the nodes `graph` shows, by node key (a node added to the draft has none). */
function runNodesOf(graph: FlowGraph, run: FlowRunInfo | null | undefined): Map<string, FlowRunNodeInfo> {
  const shown = new Set(graph.nodes.map((node) => node.id))
  const out = new Map<string, FlowRunNodeInfo>()
  for (const nodeRun of run?.nodeRuns ?? []) if (shown.has(nodeRun.nodeKey)) out.set(nodeRun.nodeKey, nodeRun)
  return out
}

/** Each node's run as the Canvas read and the run read tell it (the run read wins: it is the latest). */
function nodeRunsOf(graph: FlowGraph, run: FlowRunInfo | null | undefined): Map<string, NodeRunFacts> {
  const out = new Map<string, NodeRunFacts>()
  for (const node of graph.nodes) {
    const nodeRun: FlowGraphNodeRun | null = node.nodeRun ?? null
    const status = nodeRun?.status
    if (nodeRun && status) out.set(node.id, { ...nodeRun, status })
  }
  for (const [key, nodeRun] of runNodesOf(graph, run)) out.set(key, nodeRun)
  return out
}

/** Ids of the protected nodes of `graph` (done / open / review in the Flow's run). */
export function protectedNodeIds(graph: FlowGraph, run?: FlowRunInfo | null): Set<string> {
  const out = new Set<string>()
  for (const [id, nodeRun] of nodeRunsOf(graph, run)) {
    if (PROTECTED_NODE_STATES.includes(nodeRun.status)) out.add(id)
  }
  return out
}

/** Ids of the nodes the work is going through now (see the head of this file). */
export function activeNodeIds(graph: FlowGraph, run?: FlowRunInfo | null): Set<string> {
  const out = new Set<string>()
  for (const [id, nodeRun] of nodeRunsOf(graph, run)) {
    const executing = (nodeRun.runningExecutionCount ?? 0) > 0 || (nodeRun.requestedExecutionCount ?? 0) > 0
    const integrating = ACTIVE_INTEGRATIONS.includes(nodeRun.integrationStatus ?? '')
    if (ACTIVE_NODE_STATES.includes(nodeRun.status) || executing || integrating) out.add(id)
  }
  return out
}

/** An execution line into a protected node: it can be neither removed nor added. */
export function isEdgeProtected(edge: Pick<FlowGraphEdge, 'edgeClass' | 'target'>, protectedIds: ReadonlySet<string>): boolean {
  return edge.edgeClass === 'execution' && protectedIds.has(edge.target)
}

/**
 * What the Canvas draws of a Flow's run besides its Graph: the lock, the protected nodes, the running nodes and the
 * run read's node runs of the shown nodes (their result integration and Hold for the card chips).
 */
export interface FlowRunMarks {
  readonly lock: FlowEditLock
  readonly protectedNodeIds: ReadonlySet<string>
  readonly activeNodeIds: ReadonlySet<string>
  readonly nodeRuns: ReadonlyMap<string, FlowRunNodeInfo>
}

/**
 * The marks of a Flow's run on its shown Graph (`graph` null: none shown — the lock alone). `known`: whether the run's
 * status is confirmed; until it is, the structure waits (`unknown`).
 */
export function flowRunMarks(graph: FlowGraph | null, run: FlowRunInfo | null | undefined, known = true): FlowRunMarks {
  const lock = editLockOf(run, known)
  if (!graph) return lock === null ? NO_RUN_MARKS : { ...NO_RUN_MARKS, lock }
  return {
    lock,
    protectedNodeIds: protectedNodeIds(graph, run),
    activeNodeIds: activeNodeIds(graph, run),
    nodeRuns: runNodesOf(graph, run),
  }
}

export const NO_RUN_MARKS: FlowRunMarks = Object.freeze({
  lock: null,
  protectedNodeIds: new Set<string>(),
  activeNodeIds: new Set<string>(),
  nodeRuns: new Map<string, FlowRunNodeInfo>(),
})
