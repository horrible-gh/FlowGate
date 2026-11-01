// flowgate.v02 B0001 T#2 — 다음 단계 / 전체 실행 of a v0.1 Flow. The Run menu is the one place a run starts; the Graph
// engine refuses a v0.1-owned Flow (`flow_owned_by_v01`), so on such a Flow the same two commands start the existing
// v0.1 single run (`reviewRunStart`, scope edit | rework) that the removed header ▶ 실행 / ↻ 재작업 buttons called.
// Nothing here is a new engine: it only picks the document and calls the existing API; the server keeps the state
// transitions, the AI-running refusal and the history.
//
// - 다음 단계: the first document of the v0.1 sequence (slot order) that is not approved (approved / wf_done are skipped)
//   → `rework` when its latest review status is rejected, else `edit`; one run.
// - 전체 실행: the same pick and run, document after document. A start answer is not a finished run: the run is followed
//   until the group has no AI run, the document is read again, and the next one starts only after this one is approved
//   (waiting for its approval is waiting — nothing is approved or submitted from here). A refused start, a run that
//   ended failed, a rejected document or a wait that never ends stops the loop; it never repeats by itself, and the
//   menu runs it again.
// - One command per Flow at a time (`busy`); a Flow whose group already has an AI run starts nothing.
import { reactive } from 'vue'
import { inspectorCardKind } from '../inspector/inspectorTarget'
import type { FlowGraph, FlowGraphNode } from '../center/graph/flowGraph'

export type V01RunScope = 'edit' | 'rework'
export type V01RunMode = 'next' | 'all'

export interface V01RunTarget {
  readonly docId: string
  readonly scope: V01RunScope
}

/** Why a command did not (fully) run; `ok` = it ran (for 전체 실행: every document is approved). */
export type V01RunResult =
  | { readonly ok: true }
  | { readonly ok: false; readonly reason: 'busy' | 'ai_running' | 'none' | 'refused' | 'failed' | 'rejected' | 'timeout' | 'stopped'; readonly error?: unknown }

export interface V01RunDeps {
  /** The Flow's latest review status of a document (null: unknown). */
  readonly readStatus: (docId: string) => Promise<string | null>
  /** The group's AI run, if any. */
  readonly readActivity: (flowId: string) => Promise<{ readonly active: boolean; readonly runId: string | null; readonly status: string | null }>
  readonly start: (flowId: string, docId: string, scope: V01RunScope) => Promise<{ readonly runId: string | null }>
  readonly sleep?: (ms: number) => Promise<void>
  readonly pollMs?: number
  /** How long one run / one approval wait may last before 전체 실행 stops. */
  readonly maxWaitMs?: number
}

const CLOSED: ReadonlySet<string> = new Set(['approved', 'wf_done'])
const RUN_KINDS: ReadonlySet<string> = new Set(['research', 'task', 'design'])
const FAILED_RUN: ReadonlySet<string> = new Set(['failed', 'error', 'cancelled', 'canceled'])

export const V01_POLL_MS = 2000
export const V01_MAX_WAIT_MS = 30 * 60 * 1000

/** The Flow's work documents (조사 · 작업 · 설계) in v0.1 sequence order. */
export function v01RunOrder(graph: FlowGraph | null): FlowGraphNode[] {
  if (!graph || graph.ownership !== 'v01') return []
  return graph.nodes
    .filter((node) => !!node.slot && !!node.document && RUN_KINDS.has(inspectorCardKind(node.document.typeCode)))
    .sort((a, b) => a.slot!.order - b.slot!.order || (a.slot!.itemSeq ?? 0) - (b.slot!.itemSeq ?? 0))
}

/** First document not approved, with the scope its status asks for; null when every one is closed. */
export function v01PickTarget(order: readonly FlowGraphNode[], statusOf: (node: FlowGraphNode) => string | null): V01RunTarget | null {
  for (const node of order) {
    const status = statusOf(node) ?? ''
    if (CLOSED.has(status)) continue
    return { docId: node.document!.docId, scope: status === 'rejected' ? 'rework' : 'edit' }
  }
  return null
}

export interface V01FlowRun {
  /** Flows with a command in progress (reactive: the menu waits while one is answered). */
  readonly busy: Readonly<Record<string, boolean>>
  run(flowId: string, graph: FlowGraph | null, mode: V01RunMode): Promise<V01RunResult>
  /** Ends a 전체 실행 wait (a closed tab, a project change). */
  cancel(flowId: string): void
}

export function createV01FlowRun(deps: V01RunDeps): V01FlowRun {
  const busy = reactive<Record<string, boolean>>({})
  const stopped = new Set<string>()
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)))
  const pollMs = deps.pollMs ?? V01_POLL_MS
  const maxWaitMs = deps.maxWaitMs ?? V01_MAX_WAIT_MS

  /** Waits until the group has no AI run (the one just started); the status the run ended with, or 'timeout' | 'stopped'. */
  async function untilRunEnds(flowId: string, runId: string | null): Promise<string | null> {
    let waited = 0
    let last: string | null = null
    for (;;) {
      if (stopped.has(flowId)) return 'stopped'
      const activity = await deps.readActivity(flowId)
      last = activity.status
      if (!activity.active || (runId !== null && activity.runId !== null && activity.runId !== runId)) return last
      if (waited >= maxWaitMs) return 'timeout'
      await sleep(pollMs)
      waited += pollMs
    }
  }

  /** Waits for the document's approval; `approved` | `rejected` | 'timeout' | 'stopped'. */
  async function untilApproved(flowId: string, docId: string): Promise<'approved' | 'rejected' | 'timeout' | 'stopped'> {
    let waited = 0
    for (;;) {
      const status = (await deps.readStatus(docId)) ?? ''
      if (CLOSED.has(status)) return 'approved'
      if (status === 'rejected') return 'rejected'
      if (stopped.has(flowId)) return 'stopped'
      if (waited >= maxWaitMs) return 'timeout'
      await sleep(pollMs)
      waited += pollMs
    }
  }

  async function pick(flowId: string, graph: FlowGraph | null): Promise<V01RunTarget | null> {
    const order = v01RunOrder(graph)
    const statuses = new Map<string, string | null>()
    for (const node of order) {
      const docId = node.document!.docId
      // The latest status is the server's; the Canvas one only stands in when it cannot be read.
      statuses.set(docId, await deps.readStatus(docId).catch(() => node.document!.docReviewStatus))
      if (!CLOSED.has(statuses.get(docId) ?? '')) break
    }
    return v01PickTarget(order, (node) => statuses.get(node.document!.docId) ?? node.document!.docReviewStatus)
  }

  async function run(flowId: string, graph: FlowGraph | null, mode: V01RunMode): Promise<V01RunResult> {
    if (busy[flowId]) return { ok: false, reason: 'busy' }
    busy[flowId] = true
    stopped.delete(flowId)
    try {
      for (;;) {
        // Re-read the sequence each round: the Canvas graph handed in is the one the command was given on.
        const activity = await deps.readActivity(flowId).catch(() => null)
        if (activity?.active) return { ok: false, reason: 'ai_running' }
        const target = await pick(flowId, graph)
        if (!target) return mode === 'all' ? { ok: true } : { ok: false, reason: 'none' }
        let started: { runId: string | null }
        try {
          started = await deps.start(flowId, target.docId, target.scope)
        } catch (error) {
          return { ok: false, reason: 'refused', error }
        }
        if (mode === 'next') return { ok: true }
        const ended = await untilRunEnds(flowId, started.runId)
        if (ended === 'timeout' || ended === 'stopped') return { ok: false, reason: ended }
        if (ended !== null && FAILED_RUN.has(ended)) return { ok: false, reason: 'failed' }
        const decision = await untilApproved(flowId, target.docId)
        if (decision !== 'approved') return { ok: false, reason: decision }
      }
    } finally {
      delete busy[flowId]
      stopped.delete(flowId)
    }
  }

  return {
    busy,
    run,
    cancel: (flowId) => {
      if (busy[flowId]) stopped.add(flowId)
    },
  }
}
