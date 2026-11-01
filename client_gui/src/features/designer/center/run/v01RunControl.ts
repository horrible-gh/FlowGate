// flowgate.v02test.0001 B0001 §2 / T0004 T#2 — the menu's 다음 단계 / 전체 실행 on a v0.1 (sequence) Flow.
//
// A v0.1 Flow has no Graph run (the server refuses it with 409 flow_owned_by_v01), so its work documents run through
// the existing v0.1 single AI run (`ai-invoke/start`, scope `edit` / `rework`) — the path the header's ▶ 실행 / ↻ 재작업
// buttons used to be the only entry of. Those buttons are gone; this is the same call behind the two menu commands
// (Q535 answer o1): no new server contract, no approval bypass, no automatic retry.
//
// - 다음 단계 (`next`): the first work document of the sequence (document order) that is not approved / wf_done runs
//   once: rework when its latest status is `rejected`, edit otherwise.
// - 전체 실행 (`all`): the same pick and call, serial in document order. The start answer is not the end of a run: the
//   next target goes only after the group's AI run ended and the document's latest status is read. A document that
//   waits for its approval is waited for (never approved from here); a rejected document, a failed run (judged by the
//   run's own record, GET /ai-invoke/{run_id} — /active has no status once a run ended) or a refused start stops it — nothing repeats by itself, the menu runs it again.
// The Designer Center imports `features/document` nowhere: the Shell hands the calls in (`V01RunDeps`).
import type { FlowGraph } from '../graph/flowGraph'

export type V01RunMode = 'next' | 'all'
export type V01RunScope = 'edit' | 'rework'

/** The group's AI activity as the run waits on it. */
export interface V01Activity {
  readonly active: boolean
  readonly status: string | null
}

/** How a started run ended: `succeeded` false = it failed or was cancelled; null = not known (not finished / not readable). */
export interface V01RunEnd {
  readonly finished: boolean
  readonly succeeded: boolean | null
}

export interface V01RunDeps {
  /** Starts one v0.1 single run on the document (`reviewRunStart`) and answers its run id. A refusal is thrown. */
  readonly start: (flowId: string, docId: string, scope: V01RunScope) => Promise<string | null>
  /**
   * The started run's own final record (`reviewRunRead`, GET /ai-invoke/{run_id}). The group's /active answer only says a
   * run is no longer active — it carries no status for a run that ended, failed or not — so the end of a run is judged here.
   */
  readonly readRun?: (runId: string) => Promise<V01RunEnd>
  /** The document's latest review status (`reviewDocumentRead`) and whether its group was disposed. */
  readonly readDocument: (docId: string) => Promise<{ readonly reviewStatus: string | null; readonly disposed: boolean }>
  /** Whether an AI run owns the group now (`reviewActivityRead`). */
  readonly readActivity: (flowId: string) => Promise<V01Activity>
  /** How often a waiting run reads again (default V01_POLL_MS). */
  readonly pollMs?: number
}

export const V01_POLL_MS = 2000

/** How many times a finished run's own record is read before an unreadable one counts as failed. */
export const V01_END_READS = 3

/** Document types of the work cards a v0.1 run applies to (research · task · design, as the Inspector kinds). */
const WORK_DOC_TYPES: ReadonlySet<string> = new Set(['N', 'NR', 'T', 'TR', 'T2', 'TR2', 'DS', 'D', 'P', 'L', 'DB'])

/** Review statuses that close a document for the run: approved (done). */
const CLOSED: ReadonlySet<string> = new Set(['approved', 'wf_done'])

export const v01Closed = (status: string | null | undefined): boolean => CLOSED.has(status ?? '')

/** The scope a document runs with: rework after a rejection, else a single edit run. */
export const v01Scope = (status: string | null | undefined): V01RunScope => (status === 'rejected' ? 'rework' : 'edit')

export interface V01RunDoc {
  readonly docId: string
  readonly reviewStatus: string | null
}

/**
 * The work documents of a v0.1 Flow's Graph read, in sequence order. A document replaced by a later one (supersedes)
 * is not a target; a card without a document has nothing to run.
 */
export function v01RunDocs(graph: FlowGraph | null): V01RunDoc[] {
  if (!graph) return []
  const replaced = new Set(graph.nodes.flatMap((node) => node.document?.supersedes ?? []))
  return graph.nodes
    .map((node, index) => ({ node, index }))
    .filter(({ node }) => {
      const doc = node.document
      if (!doc || replaced.has(doc.docId)) return false
      return WORK_DOC_TYPES.has((doc.typeCode ?? node.docType ?? '').trim().toUpperCase())
    })
    .sort((a, b) => (a.node.slot?.order ?? Number.MAX_SAFE_INTEGER) - (b.node.slot?.order ?? Number.MAX_SAFE_INTEGER) || a.index - b.index)
    .map(({ node }) => ({ docId: node.document!.docId, reviewStatus: node.document!.docReviewStatus }))
}

export type V01RunOutcome =
  | { readonly kind: 'started' } // 다음 단계: one run was started
  | { readonly kind: 'completed'; readonly started: number } // 전체 실행: nothing is left to run
  | { readonly kind: 'none' } // nothing to run (every work document is approved, or there is none)
  | { readonly kind: 'busy' } // an AI run owns the group
  | { readonly kind: 'stopped'; readonly started: number; readonly reason: 'rejected' | 'failed' | 'closed' }
  | { readonly kind: 'failed'; readonly started: number; readonly error: unknown }

export interface V01RunArgs {
  readonly flowId: string
  readonly mode: V01RunMode
  /** The Flow's Graph as shown now (the sequence order and the documents). */
  readonly graph: () => FlowGraph | null
  readonly deps: V01RunDeps
  /** False once the command no longer belongs on screen (tab closed, project changed, session disposed). */
  readonly alive: () => boolean
  /** Told after each started run (the Flow is read again). */
  readonly progressed?: () => void
  /** Replaces the wait (tests). */
  readonly sleep?: (ms: number) => Promise<void>
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export async function runV01Flow(args: V01RunArgs): Promise<V01RunOutcome> {
  const { flowId, mode, deps } = args
  const sleep = args.sleep ?? wait
  const pollMs = deps.pollMs ?? V01_POLL_MS
  let started = 0
  const closed = (): V01RunOutcome => ({ kind: 'stopped', started, reason: 'closed' })

  try {
    for (;;) {
      if (!args.alive()) return closed()
      // One run per group: while an AI run owns it nothing starts (the server refuses it as well).
      if ((await deps.readActivity(flowId)).active) return started === 0 ? { kind: 'busy' } : { kind: 'stopped', started, reason: 'failed' }

      // The first work document not approved yet, by its latest status (the Graph read may be older than the document).
      let target: { docId: string; status: string | null } | null = null
      for (const doc of v01RunDocs(args.graph())) {
        const read = await deps.readDocument(doc.docId)
        if (read.disposed) throw Object.assign(new Error('disposed'), { kind: 'disposed' })
        if (v01Closed(read.reviewStatus)) continue
        target = { docId: doc.docId, status: read.reviewStatus }
        break
      }
      if (!target) return started === 0 ? { kind: 'none' } : { kind: 'completed', started }
      if (!args.alive()) return closed()

      const runId = await deps.start(flowId, target.docId, v01Scope(target.status))
      started += 1
      args.progressed?.()
      if (mode === 'next') return { kind: 'started' }

      // 전체 실행: the next one goes after this run ended and its document is read.
      let activity: V01Activity
      do {
        await sleep(pollMs)
        if (!args.alive()) return closed()
        activity = await deps.readActivity(flowId)
      } while (activity.active)
      // The run's own result decides failure / cancellation: stop (releasing the command and its lock) and never repeat by
      // itself — the menu runs it again. A result that cannot be read is read again (a one-off network error must not
      // hide a failure); if it stays unknown the command stops as failed — never waits on for an approval that may never
      // come while the lock stays held — and the menu runs it again.
      if (runId && deps.readRun) {
        let end: V01RunEnd | null = null
        for (let attempt = 0; attempt < V01_END_READS; attempt += 1) {
          if (attempt > 0) {
            await sleep(pollMs)
            if (!args.alive()) return closed()
          }
          end = await deps.readRun(runId).catch(() => null)
          if (end && end.finished) break
        }
        if (!end || !end.finished || end.succeeded !== true) return { kind: 'stopped', started, reason: 'failed' }
      }

      // The document waits for its approval (a person's) — wait for it, never approve from here.
      let first = true
      for (;;) {
        const read = await deps.readDocument(target.docId)
        if (v01Closed(read.reviewStatus)) break
        if (read.reviewStatus === 'rejected') return { kind: 'stopped', started, reason: 'rejected' }
        // A run that ended and left the document as it was (and not in review) did nothing: stop, never run it again by itself.
        if (first && read.reviewStatus === target.status && target.status !== 'pending_review') return { kind: 'stopped', started, reason: 'failed' }
        first = false
        await sleep(pollMs)
        if (!args.alive()) return closed()
      }
      args.progressed?.()
    }
  } catch (error) {
    return { kind: 'failed', started, error }
  }
}
