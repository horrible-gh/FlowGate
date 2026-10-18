// flowgate.v02.0068 T#2 (0005-L §2.2 / §2.3) — per connected group: its stages (the existing layeredDepths called
// with surrogate keys in K′ order, so a cycle's fallback order never follows a node id), forward / back edges (a
// back edge — target stage ≤ source stage — is a cycle's way back: no stage, lane, spacing or pierce rule
// applies to it), the downstream hash and the full key K, the group order (the group with a root start first)
// and the lanes: the main path keeps lane 0 and a branch moves down / right of its main parent, ordered by the
// parent's lane, the parent's stage, the barycentre of its predecessors' lanes and K. Pure.
import { layeredDepths } from './autoLayout'
import {
  classifyNodes,
  compareBaseKey,
  compareFullKey,
  fnv1a32,
  isRootStart,
  keyBaseOf,
  signatureOf,
  type LayoutInput,
  type LayoutNode,
} from './layoutInput'

export interface LayoutComponent {
  readonly nodes: readonly LayoutNode[]
  readonly stage: ReadonlyMap<string, number>
  readonly maxStage: number
  /** The group's layout edges that are not back edges. */
  readonly forward: readonly (readonly [string, string])[]
  readonly preds: ReadonlyMap<string, readonly string[]>
  readonly rootStarts: ReadonlySet<string>
}

export interface LayoutOrder {
  /** Connected groups, main group first (0005-L §2.2 comp_key). */
  readonly components: readonly LayoutComponent[]
  /** Unconnected areas, each in K order. */
  readonly areas: readonly (readonly LayoutNode[])[]
  /** K comparison of two nodes. */
  readonly compare: (a: string, b: string) => number
}

const cmpNum = (a: number, b: number) => (a < b ? -1 : a > b ? 1 : 0)

/** Stages, forward edges and keys of every group; groups and areas in their order. */
export function orderLayout(input: LayoutInput): LayoutOrder {
  const { components: groups, areas } = classifyNodes(input)
  const keys = new Map(input.nodes.map((v) => [v.id, keyBaseOf(v)] as const))
  const hashes = new Map<string, number>()
  const compare = (a: string, b: string) => compareFullKey(keys.get(a)!, hashes.get(a)!, keys.get(b)!, hashes.get(b)!)

  const components = groups.map((nodes) => stagesOf(nodes, input, keys))
  // down_hash: stages descending, so every forward child is known first (a node outside a group has no child).
  for (const comp of components) {
    const children = new Map<string, string[]>()
    for (const [source, target] of comp.forward) {
      if (!children.has(source)) children.set(source, [])
      children.get(source)!.push(target)
    }
    const byStage = [...comp.nodes].sort((a, b) => comp.stage.get(b.id)! - comp.stage.get(a.id)!)
    for (const v of byStage) {
      const below = (children.get(v.id) ?? []).map((c) => hashes.get(c)!).sort(cmpNum).join(',')
      hashes.set(v.id, fnv1a32(`${signatureOf(keys.get(v.id)!)}|${below}`))
    }
  }
  for (const v of input.nodes) if (!hashes.has(v.id)) hashes.set(v.id, fnv1a32(`${signatureOf(keys.get(v.id)!)}|`))

  const minOf = (ids: string[]) => ids.reduce((best, id) => (compare(id, best) < 0 ? id : best))
  const leadOf = (comp: LayoutComponent) => {
    const starts = [...comp.rootStarts]
    return { root: starts.length > 0 ? 0 : 1, id: minOf(starts.length ? starts : comp.nodes.filter((v) => comp.stage.get(v.id) === 0).map((v) => v.id)) }
  }
  const leads = new Map(components.map((comp) => [comp, leadOf(comp)] as const))
  components.sort((a, b) => leads.get(a)!.root - leads.get(b)!.root || compare(leads.get(a)!.id, leads.get(b)!.id))
  return {
    components,
    areas: areas.map((area) => [...area].sort((a, b) => compare(a.id, b.id))),
    compare,
  }
}

function stagesOf(nodes: readonly LayoutNode[], input: LayoutInput, keys: ReadonlyMap<string, ReturnType<typeof keyBaseOf>>): LayoutComponent {
  const ids = new Set(nodes.map((v) => v.id))
  const edges = input.edges.filter(([source, target]) => ids.has(source) && ids.has(target))
  const base = [...nodes].sort((a, b) => compareBaseKey(keys.get(a.id)!, keys.get(b.id)!))
  const digits = String(nodes.length).length
  const surrogate = new Map(base.map((v, i) => [v.id, `k${String(i).padStart(digits, '0')}`] as const))
  const depth = layeredDepths(
    base.map((v) => surrogate.get(v.id)!),
    edges.map(([source, target]) => [surrogate.get(source)!, surrogate.get(target)!] as const),
  )
  const stage = new Map(nodes.map((v) => [v.id, depth.get(surrogate.get(v.id)!) ?? 0] as const))
  const forward = edges.filter(([source, target]) => stage.get(target)! > stage.get(source)!)
  const preds = new Map<string, string[]>()
  for (const [source, target] of forward) {
    if (!preds.has(target)) preds.set(target, [])
    preds.get(target)!.push(source)
  }
  const indeg = new Map<string, number>()
  const outdeg = new Map<string, number>()
  for (const [source, target] of edges) {
    outdeg.set(source, (outdeg.get(source) ?? 0) + 1)
    indeg.set(target, (indeg.get(target) ?? 0) + 1)
  }
  const rootStarts = new Set(nodes.filter((v) => isRootStart(v, indeg.get(v.id) ?? 0, outdeg.get(v.id) ?? 0, input)).map((v) => v.id))
  return { nodes, stage, maxStage: Math.max(0, ...stage.values()), forward, preds, rootStarts }
}

/** The group's nodes of one stage. */
export const stageNodes = (comp: LayoutComponent, st: number) => comp.nodes.filter((v) => comp.stage.get(v.id) === st)

/** 0005-L §2.3 lanes. `floors`: the lowest lane the pierce repair gave a node (2.5a). */
export function lanesOf(comp: LayoutComponent, compare: (a: string, b: string) => number, floors: ReadonlyMap<string, number>): Map<string, number> {
  const lane = new Map<string, number>()
  const s = (id: string) => comp.stage.get(id)!
  const order0 = stageNodes(comp, 0)
    .map((v) => v.id)
    .sort((a, b) => (comp.rootStarts.has(a) ? 0 : 1) - (comp.rootStarts.has(b) ? 0 : 1) || compare(a, b))
  let prev = -1
  for (const id of order0) {
    lane.set(id, Math.max(prev + 1, floors.get(id) ?? 0))
    prev = lane.get(id)!
  }
  // Main parent: the smallest (lane, −stage, K) predecessor.
  const better = (u: string, p: string) => (cmpNum(lane.get(u)!, lane.get(p)!) || cmpNum(s(p), s(u)) || compare(u, p)) < 0
  for (let st = 1; st <= comp.maxStage; st += 1) {
    // p: main parent; sum / count: the predecessors' lanes (barycentre, compared as a fraction).
    const info = new Map<string, { p: string | null; sum: number; count: number }>()
    for (const v of stageNodes(comp, st)) {
      const preds = comp.preds.get(v.id) ?? []
      let p: string | null = null
      let sum = 0
      for (const u of preds) {
        sum += lane.get(u)!
        if (p === null || better(u, p)) p = u
      }
      info.set(v.id, { p, sum, count: preds.length })
    }
    const order = [...info.keys()].sort((a, b) => {
      const A = info.get(a)!
      const B = info.get(b)!
      const laneA = A.p === null ? Infinity : lane.get(A.p)!
      const laneB = B.p === null ? Infinity : lane.get(B.p)!
      const byBary = A.p === null || B.p === null ? 0 : cmpNum(A.sum * B.count, B.sum * A.count)
      return (
        cmpNum(laneA, laneB) ||
        cmpNum(A.p === null ? 0 : -s(A.p), B.p === null ? 0 : -s(B.p)) ||
        byBary ||
        compare(a, b)
      )
    })
    prev = -1
    for (const id of order) {
      const { p } = info.get(id)!
      const want = p === null ? prev + 1 : lane.get(p)!
      lane.set(id, Math.max(want, prev + 1, floors.get(id) ?? 0))
      prev = lane.get(id)!
    }
  }
  return lane
}
