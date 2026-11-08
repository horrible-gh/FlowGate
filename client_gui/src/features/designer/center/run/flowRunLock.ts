// flowgate.v02.0061 T#14 — what a Flow's run means for editing and for the Canvas lines, by the server's rules
// (flowgate.graph_run.editing, T#3). Pure: no Vue, no transport.
//
// - Run lock: a run that is running, paused or stopping locks every structure edit of its Flow (Canvas: nodes and
//   lines added / removed / changed; Inspector: card settings). Viewing, panning, zooming and moving cards stay.
//   flowgate.v02.0113 and after (0132 NR §4.4.3): an idle run waiting for people never locks by itself — and neither
//   does a confirm gate whose decision is parked for a recovery (`gate.parked`, no Git work going on). What locks
//   besides the status is the server's `gate.git_active`: a gate decision whose Git work (the automatic sync) is going
//   on now locks the structure like a running run (`run`), idle or not (server `graph_run_locked`, reason
//   `gate_git_active`). A parked gate keeps only its own settings lock (`decision_reserved`). The gate's activity moves
//   with its job events only (no run event, e.g. an idle run left by a Stop): the Center session says which read tells
//   it (`GateActivitySource`) — the Canvas read while the run read is pending / failed / older, else the run read.
// - Protected nodes: a node that is done, open or review — and every execution line into it — never changes, whatever
//   the run's status (also when it is idle). The server judges it by the run's node states; the screen reads them from
//   the Canvas node runs and from the run read, so a draft view (no node runs on its nodes) is protected the same.
// - Running lines: a line into a node the work is going through now — open or review, or one with a running /
//   requested AI execution or a pending / integrating result integration — is drawn as running (FlowEdgePath).
//   flowgate.v02.0113 T#1 (0002-D 개정 §3.13): when the server tells `node_running`, it replaces "open or review": a node
//   a person or a setup waits on is reached (the green progress line) but not running.
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
export type FlowEditLock = 'run' | 'unknown' | 'archived' | null

export function isRunLocked(run: Pick<FlowRunInfo, 'status'> | null | undefined): boolean {
  return !!run && RUN_LOCKED_STATUSES.includes(run.status)
}

/**
 * flowgate.v02.0132 T#4: a gate of the run whose decision has Git work going on now (the node run's `gate.git_active`).
 * A parked gate (`gate.parked`) is not active: its run is idle and editable.
 */
export function isGateGitActive(run: Pick<FlowRunInfo, 'nodeRuns'> | null | undefined): boolean {
  return !!run?.nodeRuns?.some((nodeRun) => nodeRun.gate?.git_active === true)
}

/**
 * Which read tells the gates' Git activity: the run read (`run`, the default) or the Canvas read (`canvas`).
 * A gate decision moving between parked and active raises no run event (its own job event only), so the two reads
 * cannot be ordered by the run's event sequence; the Center session says `canvas` while the Canvas read is the later
 * one (the run read is still pending, failed, or older than the Canvas read) and `run` once the run read has answered.
 */
export type GateActivitySource = 'run' | 'canvas'

/**
 * Whether a gate has Git work going on, by the read that is the later one. From the Canvas, a node the Canvas tells a
 * gate of (`nodeRun.gate`) is judged by that alone — a parked gate there unlocks even when the run read (older) still
 * says active; a gate the Canvas tells nothing of (a draft view) stays with the run read's node runs.
 */
export function gateGitActiveBy(graph: FlowGraph | null | undefined, run: Pick<FlowRunInfo, 'nodeRuns'> | null | undefined, source: GateActivitySource = 'run'): boolean {
  if (source === 'run' || !graph) return isGateGitActive(run)
  const told = new Set<string>()
  for (const node of graph.nodes) {
    const gate = node.nodeRun?.gate
    if (!gate) continue
    if (gate.git_active === true) return true
    if (typeof gate.git_active === 'boolean') told.add(node.id)
  }
  return !!run?.nodeRuns?.some((nodeRun) => nodeRun.gate?.git_active === true && !told.has(nodeRun.nodeKey))
}

/**
 * `known`: the run status is confirmed (`run` null = no run); a locked run locks even when it is not.
 * `gateActive`: whether a gate has Git work going on (see `gateGitActiveBy`); omitted, the run read's node runs tell it.
 */
export function editLockOf(run: Pick<FlowRunInfo, 'status' | 'nodeRuns'> | null | undefined, known = true, gateActive: boolean = isGateGitActive(run)): FlowEditLock {
  if (isRunLocked(run) || gateActive) return 'run'
  return known ? null : 'unknown'
}

type NodeRunFacts = Pick<FlowRunNodeInfo, 'status' | 'integrationStatus' | 'runningExecutionCount' | 'requestedExecutionCount' | 'nodeRunning'>

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
    // flowgate.v02.0113 T#1: the server's `node_running` (actual running) when sent — a waiting open node is no running line
    const working = typeof nodeRun.nodeRunning === 'boolean' ? nodeRun.nodeRunning : ACTIVE_NODE_STATES.includes(nodeRun.status)
    if (working || executing || integrating) out.add(id)
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
 * status is confirmed; until it is, the structure waits (`unknown`). `gateSource`: which read tells the gates' Git
 * activity (see `GateActivitySource`).
 */
export function flowRunMarks(graph: FlowGraph | null, run: FlowRunInfo | null | undefined, known = true, gateSource: GateActivitySource = 'run'): FlowRunMarks {
  const lock = editLockOf(run, known, gateGitActiveBy(graph, run, gateSource))
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
