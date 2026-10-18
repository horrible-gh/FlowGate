// flowgate.v02.0068 T#2 (0005-L §2.1 / §2.2) — 가로·세로 자동정렬의 입력: the cards of the shown Graph and the ones
// the read left out (a collapsed card keeps its place in the order and in the collision check, it never moves),
// each with its size fixed at the command (the Canvas's measured size of a shown card, else the card size), its
// real rectangle (`rect0`) and its order id `ord`; the layout edges (execution edges and, for the `sequence`
// projection, the chain); the connected groups (union-find over the layout edges) and the unconnected areas.
//
// Nothing here orders by a node id. `ord` is the place in the draft / server order — kept by a `tmp_` key's
// rename (remapGraph keeps the order) and by a read (ordinal order) — and the stable keys K′ / K are the card's
// content (document seq / id, label, type, slot order), its downstream shape (`down_hash`) and `ord` last.
// Pure: no Vue, no Vue Flow.
import { NODE_CARD_HEIGHT, NODE_CARD_WIDTH, type NodeRect } from '../canvas/edgeGeometry'
import type { FlowGraph, FlowGraphNode } from '../graph/flowGraph'
import { sequenceRootOf, type FlowLayoutBasis } from './autoLayout'

export type LayoutDirection = 'horizontal' | 'vertical'

/** The Canvas's measured size of a shown card, or nothing (not measured yet). */
export type NodeSizeOf = (nodeId: string) => { readonly width: number; readonly height: number } | null | undefined

/** The root document types (server graph_model ROOT_DOC_TYPES). */
export const ROOT_DOC_TYPES: ReadonlySet<string> = new Set(['R', 'B'])

export interface LayoutNode {
  readonly id: string
  /** The shown node when shown, else the whole Flow's. */
  readonly node: FlowGraphNode
  readonly shown: boolean
  readonly movable: boolean
  /** A card (a node with a Graph definition) — not a document. */
  readonly card: boolean
  readonly width: number
  readonly height: number
  /** The card's size along the flow axis / the lane axis (0005-L §1.1 axis table). */
  readonly flowSize: number
  readonly crossSize: number
  /** Where the card really is (the shown position, else the whole Flow's). */
  readonly rect0: NodeRect
  /** Order id: cards first (shown Graph order, then left-out cards), then documents (whole Flow order). */
  readonly ord: number
}

export interface LayoutInput {
  readonly direction: LayoutDirection
  readonly projection: string
  readonly nodes: readonly LayoutNode[]
  readonly byId: ReadonlyMap<string, LayoutNode>
  /** The layout edges: execution edges (both ends present, no self edge, once each) ∪ the sequence chain. */
  readonly edges: readonly (readonly [string, string])[]
  readonly chain: readonly string[]
  readonly sequenceRoot: string | null
}

const isCard = (node: FlowGraphNode) => !!node.definition

function sizeOk(size: { readonly width: number; readonly height: number } | null | undefined): size is { width: number; height: number } {
  return !!size && Number.isFinite(size.width) && Number.isFinite(size.height) && size.width > 0 && size.height > 0
}

/** 0005-L §2.1 build_layout_input (with §2.2 order_ids). `basis`: the whole Flow when the shown Graph leaves part out. */
export function buildLayoutInput(graph: FlowGraph, basis: FlowLayoutBasis | undefined, direction: LayoutDirection, sizeOf?: NodeSizeOf): LayoutInput {
  const whole = basis?.whole ?? graph
  const projection = graph.projection ?? whole.projection ?? ''
  const shownById = new Map(graph.nodes.map((node) => [node.id, node] as const))
  const inWhole = new Set(whole.nodes.map((node) => node.id))
  const graphOnly = graph.nodes.filter((node) => !inWhole.has(node.id))
  const all = [...whole.nodes, ...graphOnly].map((base) => shownById.get(base.id) ?? base)

  const cardSeq = [...graph.nodes.filter(isCard), ...whole.nodes.filter((node) => !shownById.has(node.id) && isCard(node))]
  const docSeq = [...whole.nodes.map((node) => shownById.get(node.id) ?? node).filter((node) => !isCard(node)), ...graphOnly.filter((node) => !isCard(node))]
  const ord = new Map<string, number>()
  cardSeq.forEach((node, i) => ord.set(node.id, i))
  docSeq.forEach((node, i) => ord.set(node.id, cardSeq.length + i))

  const nodes = all.map((node): LayoutNode => {
    const shown = shownById.has(node.id)
    const measured = shown ? sizeOf?.(node.id) : null
    const [width, height] = sizeOk(measured) ? [measured.width, measured.height] : [NODE_CARD_WIDTH, NODE_CARD_HEIGHT]
    const horizontal = direction === 'horizontal'
    return {
      id: node.id,
      node,
      shown,
      movable: node.movable === true,
      card: isCard(node),
      width,
      height,
      flowSize: horizontal ? width : height,
      crossSize: horizontal ? height : width,
      rect0: { x: node.x, y: node.y, width, height },
      ord: ord.get(node.id) ?? cardSeq.length + docSeq.length,
    }
  })
  const byId = new Map(nodes.map((v) => [v.id, v] as const))

  const root = basis ? basis.sequenceRoot : sequenceRootOf(graph) ?? null
  const slots = whole.nodes.filter((node) => node.slot).map((node) => node.id)
  const chain = projection === 'sequence' ? (root === null ? slots : [root, ...slots]) : []

  const seen = new Set<string>()
  const edges: (readonly [string, string])[] = []
  const take = (source: string, target: string) => {
    const pair = `${source}\u0000${target}`
    if (source === target || !byId.has(source) || !byId.has(target) || seen.has(pair)) return
    seen.add(pair)
    edges.push([source, target])
  }
  for (const edge of [...graph.edges, ...whole.edges]) {
    if (edge.edgeClass !== 'execution') continue
    // Documents: only the draft card layers (a free document is never in a connected group).
    if (projection === 'documents' && !(byId.get(edge.source)?.card && byId.get(edge.target)?.card)) continue
    take(edge.source, edge.target)
  }
  for (let i = 0; i + 1 < chain.length; i += 1) take(chain[i], chain[i + 1])
  return { direction, projection, nodes, byId, edges, chain, sequenceRoot: root }
}

// --- stable keys (0005-L §2.2) ------------------------------------------------------------------------------------

export interface KeyBase {
  readonly seq: number
  readonly docId: string
  readonly label: string
  readonly docType: string
  readonly nodeType: string
  readonly slotOrder: number
  readonly ord: number
}

const cmpNum = (a: number, b: number) => (a < b ? -1 : a > b ? 1 : 0)
const cmpStr = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

export function keyBaseOf(v: LayoutNode): KeyBase {
  const { node } = v
  const seq = node.document?.seq
  return {
    seq: typeof seq === 'number' && Number.isFinite(seq) ? seq : Infinity,
    docId: node.document?.docId ?? '',
    label: node.definition?.label ?? node.title ?? '',
    docType: node.definition?.docType ?? node.docType ?? '',
    nodeType: node.definition?.nodeType ?? node.kind ?? '',
    slotOrder: typeof node.slot?.order === 'number' ? node.slot.order : Infinity,
    ord: v.ord,
  }
}

/** Items 1~6: document seq, document id (none last), label, document type, node type, slot order. */
function compareContent(a: KeyBase, b: KeyBase): number {
  return (
    cmpNum(a.seq, b.seq) ||
    (a.docId === b.docId ? 0 : a.docId === '' ? 1 : b.docId === '' ? -1 : cmpStr(a.docId, b.docId)) ||
    cmpStr(a.label, b.label) ||
    cmpStr(a.docType, b.docType) ||
    cmpStr(a.nodeType, b.nodeType) ||
    cmpNum(a.slotOrder, b.slotOrder)
  )
}

/** K′: the content, then ord. */
export function compareBaseKey(a: KeyBase, b: KeyBase): number {
  return compareContent(a, b) || cmpNum(a.ord, b.ord)
}

/** K: the content, the downstream hash, then ord. */
export function compareFullKey(a: KeyBase, ha: number, b: KeyBase, hb: number): number {
  return compareContent(a, b) || cmpNum(ha, hb) || cmpNum(a.ord, b.ord)
}

/** sig(v): items 1~6 joined by U+001F. */
export function signatureOf(k: KeyBase): string {
  const num = (n: number) => (Number.isFinite(n) ? String(n) : '')
  return [num(k.seq), k.docId, k.label, k.docType, k.nodeType, num(k.slotOrder)].join('\u001f')
}

/** FNV-1a 32 over the UTF-16 code units. */
export function fnv1a32(text: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash >>> 0
}

// --- connection classification (0005-L §2.2 classify) ---------------------------------------------------------------

export interface LayoutGroups {
  /** Connected groups: two or more cards joined by layout edges, or a lone chain card. */
  readonly components: readonly (readonly LayoutNode[])[]
  /** Unconnected areas: graph / sequence one; documents [isolated draft cards, free documents]. */
  readonly areas: readonly (readonly LayoutNode[])[]
}

export function classifyNodes(input: LayoutInput): LayoutGroups {
  const parent = new Map(input.nodes.map((v) => [v.id, v.id] as const))
  const find = (id: string): string => {
    let at = id
    while (parent.get(at) !== at) at = parent.get(at)!
    parent.set(id, at)
    return at
  }
  for (const [source, target] of input.edges) {
    const a = find(source)
    const b = find(target)
    if (a !== b) parent.set(a, b)
  }
  const groups = new Map<string, LayoutNode[]>()
  for (const v of input.nodes) {
    const root = find(v.id)
    if (!groups.has(root)) groups.set(root, [])
    groups.get(root)!.push(v)
  }
  const inChain = new Set(input.chain)
  const components = [...groups.values()].filter((group) => group.length >= 2 || inChain.has(group[0].id))
  const grouped = new Set(components.flat().map((v) => v.id))
  const rest = input.nodes.filter((v) => !grouped.has(v.id))
  const areas = input.projection === 'documents' ? [rest.filter((v) => v.card), rest.filter((v) => !v.card)] : [rest]
  return { components, areas }
}

/** 0005-L §2.2 is_root_start: no incoming, some outgoing layout edge in the group, and a root kind of card. */
export function isRootStart(v: LayoutNode, indeg: number, outdeg: number, input: LayoutInput): boolean {
  if (indeg !== 0 || outdeg < 1) return false
  const definition = v.node.definition
  const docType = definition?.docType ?? v.node.docType ?? null
  if (definition?.nodeType === 'document' && docType !== null && ROOT_DOC_TYPES.has(docType)) return true
  if (definition?.rootBound === true) return true
  return input.projection === 'sequence' && input.chain[0] === v.id && v.id === input.sequenceRoot
}
