// flowgate.v02.0064 T#4 — a card's own title on the Canvas (0062 D0005 §3.2 / §3.5 / §6, 0062 L0006 2.1 / 2.8, 0064
// P0006 §4.2.7 / §6.5). Pure: no Vue, no transport.
//
// - Length: the server's rule (L0006 2.1) — NFC; control and separator characters become a space; zero width space and
//   BOM are dropped; runs of spaces become one; trimmed; then 1~30 code points (a raw text over 200 is refused as is).
//   The card checks it before sending; the server checks it again and decides.
// - Version order (L0006 2.8 accept_title_state): a title state bundle replaces the one the screen shows only when its
//   version is above it — a save answer and every Graph read alike, whatever order they arrive in. Nothing is shown
//   before the server answered (no optimistic title), so nothing is ever rolled back.
// - Who may edit (D0005 §3.2 잠금 표): a card with a title state (a v0.2 Graph node; never a v0.1 free document / slot
//   card or an older server's), on a Graph or unassigned Flow, not in a past revision view, and not while it runs. The
//   running lock is the server's own (`node_running` of the node run: the card settings' lock a title save is refused by —
//   open, or in review while the card's AI review works), never the approval state: a review card waiting only for its
//   result integration awaits no approval and still takes a title. The node run is one bundle — the run read's (the
//   latest), else the Canvas node run — never fields of the two mixed. An older server without `node_running`: only an
//   open node is locked on screen. The server judges it again in any case.
// - Which cards the AI names is the server's alone; the screen never judges it.
import type { FlowGraph, FlowGraphNode, FlowGraphNodeRun, FlowGraphTitleState } from './flowGraph'

export const NODE_TITLE_MAX_CHARS = 30
/** A raw text longer than this is refused before it is normalized (the server's human raw limit). */
export const NODE_TITLE_RAW_MAX_CHARS = 200

/** The server's normalization (L0006 2.1 normalize_title). */
export function normalizeNodeTitle(raw: string): string {
  return raw
    .normalize('NFC')
    .replace(/[\p{Cc}\p{Zs}\p{Zl}\p{Zp}]/gu, ' ')
    .replace(/[​﻿]/g, '')
    .replace(/ {2,}/g, ' ')
    .trim()
}

/** Code points of an NFC text — the count the server uses (a Korean syllable is one). */
export const nodeTitleLength = (text: string): number => [...text.normalize('NFC')].length

/** Why a title would be refused (the server's codes), or null. */
export type NodeTitleRefusal = 'title_empty' | 'title_too_long'

export interface NodeTitleCheck {
  /** The normalized title (the raw text when it is over the raw limit). */
  readonly value: string
  readonly length: number
  readonly refusal: NodeTitleRefusal | null
}

/** L0006 2.1 validate_title, for a person's text. */
export function checkNodeTitle(raw: string): NodeTitleCheck {
  const rawLength = [...raw].length
  if (rawLength > NODE_TITLE_RAW_MAX_CHARS) return { value: raw, length: rawLength, refusal: 'title_too_long' }
  const value = normalizeNodeTitle(raw)
  const length = nodeTitleLength(value)
  if (length < 1) return { value, length, refusal: 'title_empty' }
  return { value, length, refusal: length > NODE_TITLE_MAX_CHARS ? 'title_too_long' : null }
}

/** L0006 2.8: `incoming` replaces the shown bundle only when its version is above it (none shown: -1). */
export function acceptsTitleState(shown: FlowGraphTitleState | null | undefined, incoming: FlowGraphTitleState): boolean {
  return incoming.version > (shown?.version ?? -1)
}

/**
 * A Graph read against the title states the screen shows for its Flow (`known`, by node key — updated here): a node whose
 * read bundle is not older is shown as read and its bundle becomes the known one; a node whose read bundle is older than
 * the known one keeps the known bundle and the title it shows. A node without a title state is shown as read (its title
 * comes from its document or name, the Graph read's rule). The same Graph when nothing was kept.
 */
export function takeTitleStates(graph: FlowGraph, known: Map<string, FlowGraphTitleState>): FlowGraph {
  let kept = false
  const nodes = graph.nodes.map((node) => {
    const incoming = node.titleState
    if (!incoming) return node
    const shown = known.get(node.id)
    if (!shown || incoming.version >= shown.version) {
      known.set(node.id, incoming)
      return node
    }
    kept = true
    return { ...node, titleState: shown, title: shown.title ?? node.title }
  })
  return kept ? { ...graph, nodes } : graph
}

/**
 * A save answer (or any other bundle) for one node: taken when its version is above the known one — then the known one
 * and the node's title and bundle change together. The Graph to show, or null when the bundle was not taken or the node
 * is not shown (the known version still moves on in the latter case).
 */
export function applyTitleState(
  graph: FlowGraph | null,
  nodeId: string,
  incoming: FlowGraphTitleState,
  known: Map<string, FlowGraphTitleState>,
): FlowGraph | null {
  if (!acceptsTitleState(known.get(nodeId), incoming)) return null
  known.set(nodeId, incoming)
  if (!graph?.nodes.some((node) => node.id === nodeId)) return null
  return {
    ...graph,
    nodes: graph.nodes.map((node) =>
      node.id === nodeId ? { ...node, titleState: incoming, title: incoming.title ?? node.title } : node,
    ),
  }
}

/** Why a card's title does not open now: it runs (`running`). Other reasons say nothing on the card. */
export type NodeTitleLock = 'running'

export interface NodeTitleEdit {
  readonly editable: boolean
  readonly lock: NodeTitleLock | null
}

const CLOSED: NodeTitleEdit = Object.freeze({ editable: false, lock: null })

/** The node run facts the running lock reads (a run read node run or a Canvas node run, as one bundle). */
export type NodeTitleRunFacts = Partial<Pick<FlowGraphNodeRun, 'status' | 'nodeRunning'>>

/** The server's running lock of a node run (see the head of this file); no node run: not running. */
export function nodeRunsNow(run: NodeTitleRunFacts | null | undefined): boolean {
  if (!run) return false
  return typeof run.nodeRunning === 'boolean' ? run.nodeRunning : run.status === 'open'
}

/**
 * Whether the card's title opens for editing (see the head of this file). `runNode`: the node's run in the run read (the
 * latest); absent, the Canvas node run is used — the whole bundle either way.
 */
export function nodeTitleEdit(graph: FlowGraph, node: FlowGraphNode, runNode?: NodeTitleRunFacts | null): NodeTitleEdit {
  if (!node.titleState) return CLOSED
  if (graph.ownership !== 'graph' && graph.ownership !== 'unassigned') return CLOSED
  const latest = graph.latestRevisionNo ?? null
  const viewed = graph.viewRevisionNo ?? null
  if (graph.viewMode === 'revision' && latest !== null && viewed !== null && viewed !== latest) return CLOSED
  return nodeRunsNow(runNode ?? node.nodeRun) ? { editable: false, lock: 'running' } : { editable: true, lock: null }
}

/** What a title save came to: saved (its answer is already shown when it was newer), or refused with the server's code. */
export type NodeTitleSaveOutcome =
  | { readonly ok: true; readonly titleState: FlowGraphTitleState }
  | { readonly ok: false; readonly code: string | null; readonly message: string }
