// flowgate.v02test.0001 B0001 §2 / T#2 — the menu's 다음 단계 · 전체 실행 on a v0.1 Flow (O1, Q535).
//
// A v0.1 Flow is not run by the Graph run (the server refuses it: 409 flow_owned_by_v01), so the menu commands pick the
// existing v0.1 single run (`ai-invoke/start`, scope `edit` / `rework`) instead — the same calls the removed ▶ 실행 /
// ↻ 재작업 buttons made, now reached only through the two menu commands:
//
// - 다음 단계 (`next`): the first work document (조사 · 작업 · 설계) of the sequence, in its order, that is not approved
//   (approved / wf_done are skipped). A rejected one is reworked (`rework`), any other is run (`edit`). One start.
// - 전체 실행 (`all`): the same pick and start, document by document, in order. A start is not a finished work: the run
//   is followed until the group's AI run ended, and the documents' latest status is read again before the next pick.
//   Nothing is approved here and no approval condition is bypassed: a document waiting for its approval is waited for.
//   A refused start, a run that did not end as completed, a document that did not move, or a Flow that is no longer
//   current stops it — never repeated by itself; the menu commands run it again.
//
// Pure of the screen: reads, the start and the clock are `V01RunDeps`, so the order and the waits are tested as they are.

/** Review statuses of a document that needs no more work. */
const V01_CLOSED: readonly string[] = ['approved', 'wf_done']

/**
 * Document types of the sequence a run is offered on (the removed header actions': 조사 N/NR · 작업 T… · 설계 DS…).
 * 0006-TR rev3: `TS` (test scenario) is a work document too — its AI writing / rewriting after a rejection used to be the
 * TS card's own button; it is now this menu's start (draft = write `edit`, rejected = rewrite `rework`). `TSR` is the
 * test run's report (not AI-written here) and stays out.
 */
const V01_RUN_TYPES: readonly string[] = ['N', 'NR', 'T', 'TR', 'T2', 'TR2', 'TS', 'DS', 'D', 'P', 'L', 'DB']

export type V01RunMode = 'next' | 'all'

export type V01RunScope = 'edit' | 'rework'

/** One document of the v0.1 sequence as the Canvas shows it. */
export interface V01Item {
  readonly docId: string
  /** Position in the sequence (the slot's order). */
  readonly order: number
  /** Document type code of the document (`N` · `T` · `DS` …), or null. */
  readonly docType: string | null
}

export interface V01Pick {
  readonly docId: string
  readonly scope: V01RunScope
  /** The latest review status the pick was made on. */
  readonly status: string | null
}

/** Why a run did not go on: `none` = nothing left to run (not an error). */
export type V01RunStop =
  | 'done'
  | 'busy'
  | 'disposed'
  | 'refused'
  | 'not_completed'
  | 'no_progress'
  | 'closed'
  | 'timeout'

export interface V01RunResult {
  /** At least one run was started. */
  readonly started: boolean
  readonly stop: V01RunStop
  /** The refusal the start threw, when `stop` is `refused`. */
  readonly error?: unknown
}

export interface V01RunDeps {
  /** The sequence's documents now (the shown Canvas), any order. */
  items(): readonly V01Item[]
  /** The document's latest review status and whether its group was disposed. */
  readStatus(docId: string): Promise<{ readonly status: string | null; readonly disposed: boolean }>
  /** Whether an AI run owns the group now, and which. */
  activity(): Promise<{ readonly active: boolean; readonly runId: string | null }>
  /** Starts the v0.1 single run on the document; throws on a refusal. Answers the run id when it has one. */
  start(docId: string, scope: V01RunScope): Promise<string | null>
  /** How a run ended (`completed` …), or null when it is not known. */
  endReason(runId: string): Promise<string | null>
  /** Whether this run still belongs to the open Flow (not closed, not read-only any more). */
  current(): boolean
  sleep(ms: number): Promise<void>
}

export const V01_POLL_MS = 2000
/** A wait (a run to end, an approval) gives up after this long — nothing is left running behind the screen. */
export const V01_WAIT_LIMIT_MS = 30 * 60 * 1000

/** The documents a run applies to, in the sequence's order. */
export function v01RunItems(items: readonly V01Item[]): V01Item[] {
  return items.filter((item) => V01_RUN_TYPES.includes((item.docType ?? '').trim().toUpperCase())).sort((a, b) => a.order - b.order)
}

/** The pick of the latest statuses (docId -> status): the first document not approved; rejected = rework. */
export function v01Pick(items: readonly V01Item[], statuses: ReadonlyMap<string, string | null>): V01Pick | null {
  for (const item of v01RunItems(items)) {
    const status = statuses.get(item.docId) ?? null
    if (status !== null && V01_CLOSED.includes(status)) continue
    return { docId: item.docId, scope: status === 'rejected' ? 'rework' : 'edit', status }
  }
  return null
}

async function readStatuses(deps: V01RunDeps): Promise<{ statuses: Map<string, string | null>; disposed: boolean }> {
  const statuses = new Map<string, string | null>()
  let disposed = false
  for (const item of v01RunItems(deps.items())) {
    const read = await deps.readStatus(item.docId)
    statuses.set(item.docId, read.status)
    if (read.disposed) disposed = true
  }
  return { statuses, disposed }
}

/** Waits (polling) until `ready` is true; false on the limit or when the Flow is no longer current. */
async function waitUntil(deps: V01RunDeps, ready: () => Promise<boolean>): Promise<'ready' | 'closed' | 'timeout'> {
  for (let waited = 0; waited <= V01_WAIT_LIMIT_MS; waited += V01_POLL_MS) {
    if (!deps.current()) return 'closed'
    if (await ready()) return 'ready'
    await deps.sleep(V01_POLL_MS)
  }
  return 'timeout'
}

/**
 * 다음 단계 / 전체 실행 on a v0.1 Flow. A run is refused while an AI run owns the group, the group is disposed or the
 * document's state is read-only (the server's own refusals stay: a refused start stops it).
 */
export async function runV01Flow(mode: V01RunMode, deps: V01RunDeps): Promise<V01RunResult> {
  if ((await deps.activity()).active) return { started: false, stop: 'busy' }
  let started = false
  // The last start of this command: its document and the status it was started on (a document must move after it).
  let last: { docId: string; status: string | null } | null = null
  for (;;) {
    if (!deps.current()) return { started, stop: 'closed' }
    const { statuses, disposed } = await readStatuses(deps)
    // 0006-TR rev1: the reads above are async — the Flow may have been closed (tab closed, Project switched) meanwhile,
    // so it is checked again before anything is picked or started.
    if (!deps.current()) return { started, stop: 'closed' }
    if (disposed) return { started, stop: 'disposed' }
    const pick = v01Pick(deps.items(), statuses)
    if (pick === null) return { started, stop: 'done' }
    if (last !== null && last.docId === pick.docId && last.status === pick.status && pick.status !== 'pending_review') {
      // The document did not move after its run: not repeated by itself.
      return { started, stop: 'no_progress' }
    }
    if (mode === 'all' && pick.status === 'pending_review') {
      // Waiting for the person's approval (or rejection): nothing is approved from here.
      const waited = await waitUntil(deps, async () => (await deps.readStatus(pick.docId)).status !== 'pending_review')
      if (waited !== 'ready') return { started, stop: waited }
      last = null
      continue
    }
    let runId: string | null
    // Last check right before the start: no await sits between it and the call.
    if (!deps.current()) return { started, stop: 'closed' }
    try {
      runId = await deps.start(pick.docId, pick.scope)
    } catch (error) {
      return { started, stop: 'refused', error }
    }
    started = true
    last = { docId: pick.docId, status: pick.status }
    if (mode === 'next') return { started, stop: 'done' }
    // The start answer is not the end of the work: follow the run until the group's AI run is over.
    const ended = await waitUntil(deps, async () => !(await deps.activity()).active)
    if (ended !== 'ready') return { started, stop: ended }
    if (runId !== null && (await deps.endReason(runId)) !== 'completed') return { started, stop: 'not_completed' }
  }
}
