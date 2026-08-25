import { computed, onScopeDispose, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { deleteRequest, getRequest, postRequest } from '@shared/api'
import {
  MINUTE_MS,
  RETENTION_DEFAULT_MINUTES,
  RETENTION_MIRROR_KEY,
  RETENTION_NEVER,
  UI_SETTINGS_PATH,
  parseRetentionMinutes,
  readRetentionMirror,
  retentionFromResponse,
  retentionMs,
  writeRetentionMirror,
  type UiSettingsResponse,
} from '@shared/aiFinishedCardRetention'

export type AiInvokePhase = 'running' | 'pause_requested' | 'paused' | 'finished' | 'lost'

export interface AiInvokeProvider {
  id: string | null
  name: string | null
}

export interface AiInvokeProviderSwitch {
  providerId: string | null
  providerName: string | null
  fromProviderId: string | null
  fromProviderName: string | null
  toProviderId: string | null
  toProviderName: string | null
  reason: string
  detail: string | null
  attemptNo: number | null
}

export interface AiInvokeRegisterError {
  status: number
  reason: string
  turn: number | null
}

export interface AiInvokeRunEntry {
  runId: string
  groupId: string
  docRef: string
  phase: AiInvokePhase
  mode: 'single' | 'continuous'
  cancelling: boolean
  provider: AiInvokeProvider | null
  attemptNo: number
  docsTarget: number
  docsReachedSoFar: number
  chainId: string | null
  chainDocsTarget: number
  chainDocsReached: number
  startedAt: string | null
  elapsedMs: number
  providerSwitches: AiInvokeProviderSwitch[]
  // The completed hop is waiting for the next run_id; this is live chain state, not terminal.
  handoffPending: boolean
  finishedPayload: Record<string, unknown> | null
  outcome: 'complete' | 'partial' | 'none' | null
  docsReached: number
  reachedDocIds: string[]
  endReason: string | null
  lastMessageReceived: boolean
  lastMessage: string | null
  sourceDirty: boolean | null
  sourceDirtyFiles: string[]
  registerErrors: AiInvokeRegisterError[]
  toolCallMisses: number
  turnLimitExhausted: boolean
  oracleMismatch: boolean
  // 0406 T0022 task 3: the actual worker slot, distinct from auto-handled N/T slots.
  workerItemSeq: number | null
  workerDocumentType: string | null
  autoHandledItemSeqs: number[]
  // Miniplayer additions (group 0252): the awaiting-answer overlay is a DERIVED flag
  // built from pendingQDocIds — never a phase value (D0007 decision 3).
  pendingQDocIds: string[]
  pausedAt: string | null
  stopKind: 'user' | 'system' | null
  stopCode: string | null
  // 0393 B0001 / T0005 §2-6: the server already computes one sentence per stop code
  // (ai_invoke_service._stop_reason_text) and ships it on the finished payload. The card
  // never read it, so a stopped run showed a bare code — or, for B0001's three dead
  // reviews, nothing at all.
  stopReason: string | null
  stopRunId: string | null
  stopLastMessageExcerpt: string | null
  finishedAtMs: number | null
  // T0005 §2: whether THIS paused card can actually resume under the current provider
  // settings snapshot. A display hint from active-all only — resume() below still
  // shows the server's own 422 message verbatim if this hint is stale (§3 item 5).
  resumeAvailable: boolean
  resumeBlockCode: string | null
  resumeBlockReason: string | null
  resumeProviderName: string | null
}

type InvokeSseDetail = {
  kind?: 'started' | 'switched' | 'finished'
  payload?: Record<string, any>
}

const POLL_INTERVAL_MS = 5_000
const CLOCK_INTERVAL_MS = 1_000
const HANDOFF_ADOPTION_DELAYS_MS = [0, 250, 1_000, 3_000] as const
// Scheduled adoption is best-effort. Only two later, successful 5s polls may close the card.
const HANDOFF_FINALIZE_AFTER_POLLS = 2
// L0009 §1: finished/cancelled/lost cards leave the list on their own; paused cards never do.
// 0290 NR0003 §5.1: 10s was effectively "gone before it was read" — the header monitor is
// the channel this user actually watches for completions, so a finished card now survives
// long enough to walk away from. Reading the card (문서 열기 [Open document]) or removing it stays instant.
//
// 0452 L0003 §1-3: this is no longer the number the sweep reads. It is DERIVED from the
// default so it still means exactly "the retention of somebody who has never saved", which
// is what the regressions that import it are about. The live TTL is `retentionTtlMs`.
export const FINISHED_CARD_TTL_MS = RETENTION_DEFAULT_MINUTES * MINUTE_MS
// The document screen's inline banner shares this registry but not its lifetime: a result
// panel pinned over the document for 30 minutes is nobody's idea of helpful (NR0003 §5.3).
// Unchanged by the setting: the user asked for the header list to keep results, not for the
// document to stay covered (0452 L0003 §2-6). It is a CEILING — see `inlineResultWindowMs`.
export const INLINE_RESULT_WINDOW_MS = 60_000
// A 30-minute TTL turns the list into a log unless it is capped (NR0003 §5.5).
export const MAX_FINISHED_CARDS = 20
// 0452 L0003 §2-7: "never expires" is an explicit choice to pile results up, so cutting it
// at 20 would break the very request. The cap still has to exist — persistFinished's quota
// failure used to lose every stored card silently — so it moves rather than disappears.
export const MAX_FINISHED_CARDS_UNBOUNDED = 200
// One bounded retry's worth of cards when sessionStorage refuses the full snapshot.
export const PERSIST_QUOTA_FALLBACK_CARDS = 20
const FINISHED_STORAGE_KEY = 'fg.ai_invoke.finished_cards'

// The single expiry predicate, shared by the sweep and by session restore (L0003 §2-3).
// The two sentinels are answered BEFORE the subtraction on purpose: the sweep's `now` is a
// 1s tick that can sit slightly behind a card's own `finishedAtMs`, which makes the age
// negative — and `-5 >= 0` is false, so a "disappears immediately" card would linger for up
// to a second. Infinity is intercepted for the same reason in the other direction.
export function isExpired(finishedAtMs: number, referenceNowMs: number, ttlMs: number): boolean {
  if (ttlMs === 0) return true
  if (!Number.isFinite(ttlMs)) return false
  return referenceNowMs - finishedAtMs >= ttlMs
}

// Slot count is a memory guard, not a time rule, so only the unbounded choice widens it.
export function cardSlotsFor(minutes: number): number {
  return minutes === RETENTION_NEVER ? MAX_FINISHED_CARDS_UNBOUNDED : MAX_FINISHED_CARDS
}

function nullableString(value: unknown): string | null {
  return value == null || value === '' ? null : String(value)
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

function normalizeProvider(payload: Record<string, any>, previous: AiInvokeProvider | null = null): AiInvokeProvider | null {
  if (payload.provider && typeof payload.provider === 'object') {
    return {
      id: payload.provider.id != null || payload.provider.provider_id != null
        ? nullableString(payload.provider.id ?? payload.provider.provider_id)
        : previous?.id ?? null,
      name: payload.provider.name != null || payload.provider.provider_name != null
        ? nullableString(payload.provider.name ?? payload.provider.provider_name)
        : previous?.name ?? null,
    }
  }
  if (payload.provider_id != null || payload.provider_name != null) {
    return {
      id: payload.provider_id != null ? nullableString(payload.provider_id) : previous?.id ?? null,
      name: payload.provider_name != null ? nullableString(payload.provider_name) : previous?.name ?? null,
    }
  }
  return previous
}

function normalizeSwitch(payload: Record<string, any>): AiInvokeProviderSwitch {
  return {
    providerId: nullableString(payload.provider_id),
    providerName: nullableString(payload.provider_name),
    fromProviderId: nullableString(payload.from_provider_id),
    fromProviderName: nullableString(payload.from_provider_name),
    toProviderId: nullableString(payload.to_provider_id),
    toProviderName: nullableString(payload.to_provider_name),
    reason: String(payload.reason ?? ''),
    detail: nullableString(payload.detail),
    attemptNo: payload.attempt_no == null ? null : Number(payload.attempt_no),
  }
}

function normalizeRegisterErrors(payload: unknown): AiInvokeRegisterError[] {
  return Array.isArray(payload)
    ? payload.map(item => ({
        status: Number(item?.status ?? 0),
        reason: String(item?.reason ?? ''),
        turn: item?.turn == null ? null : Number(item.turn),
      }))
    : []
}

function normalizeSwitches(payload: unknown): AiInvokeProviderSwitch[] {
  return Array.isArray(payload)
    ? payload.map(item => normalizeSwitch((item ?? {}) as Record<string, any>))
    : []
}

function startedEntry(
  payload: Record<string, any>,
  previous?: AiInvokeRunEntry,
): AiInvokeRunEntry {
  const runId = String(payload.run_id ?? previous?.runId ?? '')
  const sameRun = previous?.runId === String(payload.run_id ?? '')
  const docsTarget = Number(payload.docs_target ?? (sameRun ? previous?.docsTarget : 1) ?? 1)
  const docsReachedSoFar = Number(
    payload.docs_reached_so_far ?? (sameRun ? previous?.docsReachedSoFar : 0) ?? 0,
  )
  const payloadChainId = nullableString(payload.chain_id)
  const sameChain = payloadChainId != null && previous?.chainId === payloadChainId
  const chainId = payloadChainId ?? (sameRun ? previous?.chainId ?? null : null)
  return {
    runId,
    groupId: String(payload.group_id ?? previous?.groupId ?? ''),
    docRef: String(payload.doc_ref ?? (sameRun ? previous?.docRef : '') ?? ''),
    phase: payload.status === 'pause_requested' ? 'pause_requested' : 'running',
    mode: payload.mode === 'continuous' ? 'continuous' : (sameRun ? previous?.mode ?? 'single' : 'single'),
    cancelling: payload.status === 'cancelling',
    provider: normalizeProvider(payload, sameRun ? previous?.provider ?? null : null),
    attemptNo: Number(payload.attempt_no ?? (sameRun ? previous?.attemptNo : 1) ?? 1),
    docsTarget,
    docsReachedSoFar,
    chainId,
    chainDocsTarget: Number(
      payload.chain_docs_target ?? (sameChain ? previous?.chainDocsTarget : docsTarget) ?? docsTarget,
    ),
    chainDocsReached: Number(
      payload.chain_docs_reached ?? (sameChain ? previous?.chainDocsReached : docsReachedSoFar) ?? docsReachedSoFar,
    ),
    startedAt: nullableString(payload.started_at) ?? (sameRun ? previous?.startedAt ?? null : null),
    elapsedMs: Number(payload.elapsed_ms ?? (sameRun ? previous?.elapsedMs : 0) ?? 0),
    providerSwitches: sameRun ? previous?.providerSwitches ?? [] : [],
    handoffPending: sameRun ? previous?.handoffPending ?? false : false,
    finishedPayload: null,
    outcome: null,
    docsReached: 0,
    reachedDocIds: [],
    endReason: null,
    lastMessageReceived: false,
    lastMessage: null,
    sourceDirty: null,
    sourceDirtyFiles: [],
    registerErrors: [],
    toolCallMisses: 0,
    turnLimitExhausted: false,
    oracleMismatch: false,
    workerItemSeq: payload.hop_item_seq == null ? null : Number(payload.hop_item_seq),
    workerDocumentType: nullableString(payload.worker_document_type),
    autoHandledItemSeqs: Array.isArray(payload.auto_handled_item_seqs)
      ? payload.auto_handled_item_seqs.map(Number)
      : [],
    pendingQDocIds: Array.isArray(payload.pending_q_doc_ids)
      ? stringArray(payload.pending_q_doc_ids)
      : (sameRun ? previous?.pendingQDocIds ?? [] : []),
    pausedAt: sameRun ? previous?.pausedAt ?? null : null,
    stopKind: sameRun ? previous?.stopKind ?? null : null,
    stopCode: sameRun ? previous?.stopCode ?? null : null,
    stopReason: sameRun ? previous?.stopReason ?? null : null,
    stopRunId: sameRun ? previous?.stopRunId ?? null : null,
    stopLastMessageExcerpt: sameRun ? previous?.stopLastMessageExcerpt ?? null : null,
    finishedAtMs: null,
    // A live/running card carries no provider-settings blocker of its own — only a
    // fetched paused row (pausedEntry below) ever sets these to a real block.
    resumeAvailable: true,
    resumeBlockCode: null,
    resumeBlockReason: null,
    resumeProviderName: null,
  }
}

function pausedEntry(payload: Record<string, any>, previous?: AiInvokeRunEntry): AiInvokeRunEntry {
  // A paused chain has no live run (P0008 S5) — the card is keyed by group alone.
  const docsTarget = Number(payload.docs_target ?? previous?.docsTarget ?? 0)
  const docsReached = Number(payload.docs_reached ?? previous?.docsReachedSoFar ?? 0)
  return {
    runId: nullableString(payload.stop_run_id) ?? previous?.runId ?? '',
    groupId: String(payload.group_id ?? previous?.groupId ?? ''),
    docRef: String(payload.doc_ref ?? previous?.docRef ?? ''),
    phase: 'paused',
    mode: 'continuous',
    cancelling: false,
    provider: previous?.provider ?? null,
    attemptNo: previous?.attemptNo ?? 1,
    docsTarget,
    docsReachedSoFar: docsReached,
    chainId: nullableString(payload.chain_id) ?? previous?.chainId ?? null,
    chainDocsTarget: Number(payload.chain_docs_target ?? previous?.chainDocsTarget ?? docsTarget),
    chainDocsReached: Number(
      payload.chain_docs_reached
        ?? (payload.chain_id != null || previous?.chainId != null ? previous?.chainDocsReached : docsReached)
        ?? docsReached,
    ),
    startedAt: null,
    elapsedMs: previous?.elapsedMs ?? 0,
    providerSwitches: [],
    handoffPending: false,
    finishedPayload: null,
    outcome: null,
    docsReached: Number(payload.docs_reached ?? 0),
    reachedDocIds: [],
    endReason: (payload.stop_kind ?? 'user') === 'user'
      ? 'user_paused'
      : nullableString(payload.stop_code),
    lastMessageReceived: false,
    lastMessage: nullableString(payload.stop_last_message_excerpt),
    sourceDirty: null,
    sourceDirtyFiles: [],
    registerErrors: [],
    toolCallMisses: 0,
    turnLimitExhausted: false,
    oracleMismatch: false,
    workerItemSeq: payload.hop_item_seq == null ? previous?.workerItemSeq ?? null : Number(payload.hop_item_seq),
    workerDocumentType: nullableString(payload.worker_document_type) ?? previous?.workerDocumentType ?? null,
    autoHandledItemSeqs: Array.isArray(payload.auto_handled_item_seqs)
      ? payload.auto_handled_item_seqs.map(Number)
      : previous?.autoHandledItemSeqs ?? [],
    pendingQDocIds: stringArray(payload.pending_q_doc_ids),
    pausedAt: nullableString(payload.paused_at),
    stopKind: payload.stop_kind === 'system' ? 'system' : 'user',
    stopCode: nullableString(payload.stop_code),
    stopReason: nullableString(payload.stop_reason) ?? previous?.stopReason ?? null,
    stopRunId: nullableString(payload.stop_run_id),
    stopLastMessageExcerpt: nullableString(payload.stop_last_message_excerpt),
    finishedAtMs: null,
    // T0005 §2/§3 item 3: preserve active-all's blocker fields losslessly. A response
    // that predates this field (older deploy) has no key here at all, so the missing
    // case defaults to the pre-existing behavior — resumable, no blocker.
    resumeAvailable: payload.resume_available == null
      ? (previous?.resumeAvailable ?? true)
      : Boolean(payload.resume_available),
    resumeBlockCode: nullableString(payload.resume_block_code),
    resumeBlockReason: nullableString(payload.resume_block_reason),
    resumeProviderName: nullableString(payload.resume_provider_name),
  }
}

export function aiInvokeGroupId(project: string, moduleName: string | null | undefined, group: string): string {
  if (group.startsWith(`${project}.`)) return group
  return `${project}.${moduleName || 'none'}.${group}`
}

export function groupIdFromDocId(docId: string): string | null {
  const match = /^(.+)\.[^.]+$/.exec(docId)
  return match?.[1] ?? null
}

export function isAwaitingQ(entry: AiInvokeRunEntry): boolean {
  return entry.pendingQDocIds.length > 0
}

export function openTargetDocId(entry: AiInvokeRunEntry): string | undefined {
  return entry.pendingQDocIds[0]
    ?? entry.reachedDocIds[entry.reachedDocIds.length - 1]
    ?? entry.docRef
}

const ACTIVE_PHASES: AiInvokePhase[] = ['running', 'pause_requested']

export function isContinuationHandoff(
  payload: Record<string, any>,
  entry?: AiInvokeRunEntry,
  explicitHandoff = false,
): boolean {
  if (payload.end_reason !== 'exited') return false
  if (payload.stop_code === 'hop_handoff' || explicitHandoff) return true
  if (entry?.mode !== 'continuous' || payload.outcome !== 'complete') return false

  const docsTarget = Number(payload.docs_target ?? entry.docsTarget)
  const docsReached = Number(payload.docs_reached ?? entry.docsReachedSoFar)
  const chainTarget = Number(payload.chain_docs_target ?? entry.chainDocsTarget)
  const chainReached = Number(payload.chain_docs_reached ?? entry.chainDocsReached)
  // A one-document run target is ambiguous at a hop boundary; only chain coordinates may
  // infer continuation there. The server's explicit stop_code always wins above.
  const runHasMore = docsTarget > 1 && docsReached < docsTarget
  const chainHasMore = chainTarget > 0 && chainReached < chainTarget
  return runHasMore || chainHasMore
}

// The one predicate for "a result the user has not dealt with yet". 0294 B0001: every
// surface that summarizes the registry — including the CLOSED header chip — has to count
// these for exactly as long as the card lives, or the completion is the single state that
// is never shown.
export function isFinishedCard(entry: AiInvokeRunEntry): boolean {
  // A user_paused finish is a PAUSED card (P0008 S4) — never a decay/persist candidate.
  return (entry.phase === 'finished' || entry.phase === 'lost')
    && entry.handoffPending !== true
    && entry.endReason !== 'user_paused'
    && entry.finishedAtMs != null
}

// 'complete' is the only clean landing; partial/none/lost must not borrow the success
// tone, or a failed chain reads as a job well done on the chip alone.
export function isFinishedAlert(entry: AiInvokeRunEntry): boolean {
  return isFinishedCard(entry) && (entry.phase === 'lost' || entry.outcome !== 'complete')
}

function sortRank(entry: AiInvokeRunEntry): number {
  if (isAwaitingQ(entry)) return 0
  if (ACTIVE_PHASES.includes(entry.phase)) return 1
  if (entry.phase === 'paused') return 2
  return 3
}

// With a 30-minute TTL a plain group-id sort would let stale finished cards sit above a
// live run (NR0003 §5.5). State first — what needs the user comes first — then newest
// result first inside the finished band, group id elsewhere so the order stays stable.
export function compareRunEntries(a: AiInvokeRunEntry, b: AiInvokeRunEntry): number {
  const rank = sortRank(a) - sortRank(b)
  if (rank !== 0) return rank
  // Two runs can finish inside the same millisecond, so the group id still has to break
  // the tie — otherwise the order of two finished cards is whatever the object yields.
  if (sortRank(a) === 3 && a.finishedAtMs !== b.finishedAtMs) {
    return (b.finishedAtMs ?? 0) - (a.finishedAtMs ?? 0)
  }
  return a.groupId.localeCompare(b.groupId)
}

// Finished cards live only in this store, and /ai-invoke/active-all deliberately omits
// finished runs — so without this a reload wiped the very cards the TTL is meant to keep
// (NR0003 §3.5). sessionStorage, not local: per-tab is the right scope for a popover.
// Restore runs at store construction, which is BEFORE the server's answer can arrive, so
// it judges by the browser mirror (L0003 §2-4). With no usable mirror it fails OPEN — an
// unbounded TTL — because the other direction is unrecoverable: assuming 30 minutes would
// permanently delete the cards of somebody who chose "never expires" on their first reload.
// Cards kept a moment too long cost nothing; the first sweep after the setting lands
// removes them.
function retentionMsFromMirror(): number {
  const mirrored = readRetentionMirror()
  return mirrored == null ? Number.POSITIVE_INFINITY : retentionMs(mirrored)
}

function loadPersistedFinished(): Record<string, AiInvokeRunEntry> {
  if (typeof sessionStorage === 'undefined') return {}
  try {
    const raw = sessionStorage.getItem(FINISHED_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return {}
    const ttlMs = retentionMsFromMirror()
    const now = Date.now()
    const restored: Record<string, AiInvokeRunEntry> = {}
    for (const [groupId, entry] of Object.entries(parsed as Record<string, AiInvokeRunEntry>)) {
      if (entry && isFinishedCard(entry) && !isExpired(entry.finishedAtMs as number, now, ttlMs)) {
        restored[groupId] = entry
      }
    }
    return restored
  } catch {
    return {}
  }
}

function persistFinished(runsByGroup: Record<string, AiInvokeRunEntry>): void {
  if (typeof sessionStorage === 'undefined') return
  const entries = Object.entries(runsByGroup).filter(([, entry]) => isFinishedCard(entry))
  try {
    if (entries.length === 0) {
      sessionStorage.removeItem(FINISHED_STORAGE_KEY)
      return
    }
    sessionStorage.setItem(FINISHED_STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)))
    return
  } catch {
    // Fall through to one bounded retry. Swallowing the quota error outright is what
    // 0452 L0003 §5 calls out: with 200 cards allowed, the FIRST oversized write used to
    // leave the key holding the previous snapshot and nobody was told (L0003 §2-7).
  }
  try {
    const newest = [...entries]
      .sort((a, b) => (b[1].finishedAtMs as number) - (a[1].finishedAtMs as number))
      .slice(0, PERSIST_QUOTA_FALLBACK_CARDS)
    sessionStorage.setItem(FINISHED_STORAGE_KEY, JSON.stringify(Object.fromEntries(newest)))
  } catch {
    // Give up on the WRITE only. The in-memory registry still holds every card, so the
    // monitor is intact for this tab; only a reload would lose them.
  }
}

export const useAiInvokeRunsStore = defineStore('ai-invoke-runs', () => {
  const runsByGroup = reactive<Record<string, AiInvokeRunEntry>>(loadPersistedFinished())
  const now = ref(Date.now())
  const discoveryInFlight = new Set<string>()
  const refreshingRunIds = new Set<string>()
  const continuationHandoffs = new Set<string>()
  const handoffPollMisses = new Map<string, number>()
  const handoffPollEligibleAt = new Map<string, number>()
  const handoffFinishedPayloads = new Map<string, Record<string, unknown>>()
  const handoffAdoptionTimers = new Map<string, ReturnType<typeof setTimeout>[]>()
  const pauseRefreshInFlight = new Set<string>()
  let lastPollAt = 0
  let bootstrapInFlight = false
  const bootstrapPending = ref(true)
  let persistDirty = false

  // 0401 NR0003 SS3 cause 3 / T0004 task 5: the server's lease can outlive this process's
  // own view of a run (a 'lost' card, or a lease another process holds), so the button
  // gate needs the lease's own truth too -- not just ACTIVE_PHASES. Keyed by group id;
  // a group with no key here has no known active lease. Single-flight + a per-group
  // generation counter (T0004 task 5 note) so a slow response can never overwrite a
  // newer one, and the button never flickers while a fetch is in flight.
  const groupLeaseLive = reactive<Record<string, boolean>>({})
  const leaseFetchInFlight = new Set<string>()
  const leaseFetchGeneration = new Map<string, number>()

  // 0452 L0003 §2-4/§2-5. The value in force right now. The mirror is the only thing this
  // synchronous construction can consult; the server's answer replaces it a round trip
  // later, and another tab's save arrives as a storage event in between. Single-flight plus
  // a generation counter (the same shape refreshGroupLease already uses) is what stops a
  // slow GET from landing on top of a newer value that a storage event just adopted.
  const retentionMinutes = ref(readRetentionMirror() ?? RETENTION_DEFAULT_MINUTES)
  const retentionTtlMs = computed(() => retentionMs(retentionMinutes.value))
  let retentionGeneration = 0
  let retentionAdoptedGeneration = 0
  let retentionFetchInFlight = false

  // Batched through the 1s clock: the finished set changes in bursts (a sweep can drop
  // several cards at once) and sessionStorage writes are synchronous.
  function schedulePersist(): void {
    persistDirty = true
  }

  function flushPersist(): void {
    if (!persistDirty) return
    persistDirty = false
    persistFinished(runsByGroup)
  }

  function handoffKey(groupId: string, runId: string): string {
    return `${groupId}\n${runId}`
  }

  function clearHandoffTracking(groupId: string): void {
    for (const timer of handoffAdoptionTimers.get(groupId) ?? []) clearTimeout(timer)
    handoffAdoptionTimers.delete(groupId)
    handoffPollMisses.delete(groupId)
    handoffPollEligibleAt.delete(groupId)
    handoffFinishedPayloads.delete(groupId)
    const prefix = `${groupId}\n`
    for (const key of continuationHandoffs) {
      if (key.startsWith(prefix)) continuationHandoffs.delete(key)
    }
  }

  function scheduleHandoffAdoption(groupId: string): void {
    if (handoffAdoptionTimers.has(groupId)) return
    const timers = HANDOFF_ADOPTION_DELAYS_MS.map((delay, index) => setTimeout(() => {
      void bootstrap()
      if (index === HANDOFF_ADOPTION_DELAYS_MS.length - 1) {
        handoffAdoptionTimers.delete(groupId)
      }
    }, delay))
    handoffAdoptionTimers.set(groupId, timers)
  }

  function finalizeHandoff(groupId: string, runId: string): void {
    const run = runsByGroup[groupId]
    if (!run || run.runId !== runId || !run.handoffPending) return
    if (retentionTtlMs.value === 0) {
      // The hop turned out to be the end of the chain, so this IS a completion — and a
      // completion makes no card at this setting. Doing it here rather than leaving it to
      // the sweep is what keeps the "not for one tick" promise (L0003 §4-1).
      delete runsByGroup[groupId]
      clearHandoffTracking(groupId)
      schedulePersist()
      return
    }
    run.phase = 'finished'
    run.handoffPending = false
    run.finishedPayload = handoffFinishedPayloads.get(groupId) ?? run.finishedPayload
    run.finishedAtMs = Date.now()
    clearHandoffTracking(groupId)
    schedulePersist()
  }

  function trackStarted(payload: Record<string, any>): void {
    if (!payload?.run_id || !payload?.group_id) return
    const groupId = String(payload.group_id)
    const runId = String(payload.run_id)
    const continuationMarker = payload.continuation_pending === true
    const previous = runsByGroup[groupId]
    // A delayed frame for the completed hop must not erase its adoption timers. Only a
    // different run_id can authoritatively replace a settled handoff.
    if (!continuationMarker && previous?.handoffPending && previous.runId === runId) return
    if (continuationMarker) {
      continuationHandoffs.add(handoffKey(groupId, runId))
    } else {
      // A real start (including a replacement run_id) is authoritative adoption.
      clearHandoffTracking(groupId)
    }
    const replacedFinished = previous != null && isFinishedCard(previous)
    runsByGroup[groupId] = startedEntry(payload, previous)
    if (replacedFinished) schedulePersist()
  }

  function trackProviderSwitched(payload: Record<string, any>): void {
    if (!payload?.run_id || !payload?.group_id) return
    const groupId = String(payload.group_id)
    const run = runsByGroup[groupId]
    if (!run || run.runId !== String(payload.run_id) || !ACTIVE_PHASES.includes(run.phase)) return

    const switched = normalizeSwitch(payload)
    const previousSwitch = run.providerSwitches[run.providerSwitches.length - 1]
    if (
      previousSwitch?.attemptNo !== switched.attemptNo
      || previousSwitch?.toProviderId !== switched.toProviderId
      || previousSwitch?.providerId !== switched.providerId
    ) {
      run.providerSwitches.push(switched)
    }
    run.provider = {
      id: switched.toProviderId ?? switched.providerId,
      name: switched.toProviderName ?? switched.providerName,
    }
    run.attemptNo = Number(payload.attempt_no ?? run.attemptNo)
  }

  async function refreshPausedState(groupId: string): Promise<void> {
    if (pauseRefreshInFlight.has(groupId)) return
    pauseRefreshInFlight.add(groupId)
    try {
      const response = await getRequest<any>('/api/v1/ai-invoke/active-all')
      const paused = Array.isArray(response.data?.paused) ? response.data.paused : []
      const row = paused.find((item: any) => String(item?.group_id ?? '') === groupId)
      const existing = runsByGroup[groupId]
      // This targeted refresh never removes a just-settled card when an older active-all
      // response misses it; it only adopts the server's authoritative preflight fields.
      if (row && existing?.phase === 'paused') {
        runsByGroup[groupId] = pausedEntry(row, existing)
      }
    } catch {
      // Best effort: the next normal bootstrap will reconcile the same server fields.
    } finally {
      pauseRefreshInFlight.delete(groupId)
    }
  }

  function trackFinished(payload: Record<string, any>): void {
    if (!payload?.run_id || !payload?.group_id) return
    const groupId = String(payload.group_id)
    const runId = String(payload.run_id)
    const existing = runsByGroup[groupId]
    if (existing && existing.runId !== runId && existing.runId !== '') return

    const base = existing ?? startedEntry(payload)
    // The store owns hop-vs-chain completion. The server's stop_code is authoritative;
    // the marker and counters only preserve compatibility with older/lost SSE frames.
    const explicitHandoff = continuationHandoffs.delete(handoffKey(groupId, runId))
    const handoffPending = isContinuationHandoff(payload, base, explicitHandoff)
    const firstSettledHandoff = handoffPending && !handoffFinishedPayloads.has(groupId)
    // Boundary stop (P0008 S4): a user-paused finish is a PAUSED card with a resume
    // button, never a finished card — and never a TTL-sweep candidate.
    const userPaused = payload.end_reason === 'user_paused'
    // Order matters (L0003 §4-1): a user pause and a hop boundary are judged FIRST, and
    // neither makes a finished card at any setting. Only what is left over — a real
    // completion — is dropped outright when the user chose "disappears immediately".
    if (!userPaused && !handoffPending && retentionTtlMs.value === 0) {
      delete runsByGroup[groupId]
      clearHandoffTracking(groupId)
      schedulePersist()
      return
    }
    const finishedSwitches = normalizeSwitches(payload.fallback_history)
    runsByGroup[groupId] = {
      ...base,
      runId,
      groupId,
      docRef: String(payload.doc_ref ?? base.docRef),
      phase: userPaused ? 'paused' : (handoffPending ? 'running' : 'finished'),
      cancelling: false,
      provider: normalizeProvider(payload, base.provider),
      attemptNo: Number(payload.attempt_no ?? base.attemptNo),
      docsTarget: Number(payload.docs_target ?? base.docsTarget),
      docsReachedSoFar: Number(payload.docs_reached ?? payload.docs_reached_so_far ?? base.docsReachedSoFar),
      chainId: nullableString(payload.chain_id) ?? base.chainId,
      chainDocsTarget: Number(payload.chain_docs_target ?? base.chainDocsTarget ?? payload.docs_target ?? base.docsTarget),
      chainDocsReached: Number(
        payload.chain_docs_reached
          ?? (base.chainId != null
            ? base.chainDocsReached
            : payload.docs_reached ?? payload.docs_reached_so_far ?? base.docsReachedSoFar),
      ),
      // A settled hop is only a chain-level placeholder until its successor is adopted.
      // Stop its hop-local clock instead of presenting the old provider's runtime as the new hop.
      startedAt: handoffPending ? null : base.startedAt,
      elapsedMs: Number(payload.duration_ms ?? payload.elapsed_ms ?? base.elapsedMs),
      providerSwitches: finishedSwitches.length > 0 ? finishedSwitches : base.providerSwitches,
      handoffPending,
      finishedPayload: handoffPending ? null : { ...payload },
      outcome: ['complete', 'partial', 'none'].includes(payload.outcome)
        ? payload.outcome
        : null,
      docsReached: Number(payload.docs_reached ?? base.docsReachedSoFar ?? 0),
      reachedDocIds: stringArray(payload.reached_doc_ids),
      endReason: nullableString(payload.end_reason),
      lastMessageReceived: Boolean(payload.last_message_received),
      lastMessage: nullableString(payload.last_message),
      sourceDirty: payload.source_dirty == null ? null : Boolean(payload.source_dirty),
      sourceDirtyFiles: stringArray(payload.source_dirty_files),
      registerErrors: normalizeRegisterErrors(payload.register_errors),
      toolCallMisses: Number(payload.tool_call_misses ?? 0),
      turnLimitExhausted: Boolean(payload.turn_limit_exhausted),
      oracleMismatch: Boolean(payload.oracle_mismatch),
      workerItemSeq: payload.hop_item_seq == null ? base.workerItemSeq : Number(payload.hop_item_seq),
      workerDocumentType: nullableString(payload.worker_document_type) ?? base.workerDocumentType,
      autoHandledItemSeqs: Array.isArray(payload.auto_handled_item_seqs)
        ? payload.auto_handled_item_seqs.map(Number)
        : base.autoHandledItemSeqs,
      pausedAt: userPaused ? new Date().toISOString() : base.pausedAt,
      stopKind: userPaused ? 'user' : base.stopKind,
      stopCode: nullableString(payload.stop_code) ?? base.stopCode,
      stopReason: nullableString(payload.stop_reason) ?? base.stopReason,
      stopRunId: userPaused ? runId : base.stopRunId,
      stopLastMessageExcerpt: base.stopLastMessageExcerpt,
      finishedAtMs: userPaused || handoffPending ? null : Date.now(),
    }
    if (handoffPending) {
      handoffFinishedPayloads.set(groupId, { ...payload })
      if (firstSettledHandoff) {
        handoffPollMisses.set(groupId, 0)
        handoffPollEligibleAt.set(
          groupId,
          Date.now() + HANDOFF_ADOPTION_DELAYS_MS[HANDOFF_ADOPTION_DELAYS_MS.length - 1],
        )
        scheduleHandoffAdoption(groupId)
      }
    } else {
      clearHandoffTracking(groupId)
    }
    schedulePersist()
    if (userPaused) void refreshPausedState(groupId)
  }

  function markLost(groupId: string, runId?: string): void {
    const run = runsByGroup[groupId]
    if (!run || (runId && run.runId !== runId) || !ACTIVE_PHASES.includes(run.phase)) return
    if (retentionTtlMs.value === 0) {
      delete runsByGroup[groupId]
      schedulePersist()
      return
    }
    run.phase = 'lost'
    run.cancelling = false
    run.finishedAtMs = Date.now()
    schedulePersist()
  }

  function applySse(detail: InvokeSseDetail | undefined): void {
    const payload = detail?.payload
    if (!payload?.group_id) return
    if (detail?.kind === 'started') trackStarted(payload)
    else if (detail?.kind === 'switched') trackProviderSwitched(payload)
    else if (detail?.kind === 'finished') trackFinished(payload)
  }

  async function refresh(groupId: string): Promise<void> {
    const run = runsByGroup[groupId]
    if (!run || !ACTIVE_PHASES.includes(run.phase) || refreshingRunIds.has(run.runId)) return
    if (run.handoffPending) {
      // The old run endpoint can only repeat its finished payload. Group discovery is the
      // recovery path that can see and adopt the replacement run_id.
      await discover(groupId)
      return
    }
    const runId = run.runId
    refreshingRunIds.add(runId)
    try {
      const response = await getRequest<any>(`/api/v1/ai-invoke/${encodeURIComponent(runId)}`)
      const payload = response.data ?? {}
      if (payload.status === 'finished') {
        trackFinished({ ...payload, run_id: payload.run_id ?? runId, group_id: payload.group_id ?? groupId })
      } else {
        trackStarted({
          ...payload,
          run_id: payload.run_id ?? runId,
          group_id: payload.group_id ?? groupId,
          doc_ref: payload.doc_ref ?? run.docRef,
        })
      }
    } catch (error: any) {
      if (error?.response?.status === 404) markLost(groupId, runId)
    } finally {
      refreshingRunIds.delete(runId)
    }
  }

  async function refreshAllRunning(): Promise<void> {
    const activeEntries = Object.entries(runsByGroup)
      .filter(([, run]) => ACTIVE_PHASES.includes(run.phase))
    const handoffs = activeEntries
      .filter(([, run]) => run.handoffPending)
      .map(([groupId, run]) => [groupId, run.runId] as const)

    await Promise.all(
      activeEntries
        .filter(([, run]) => !run.handoffPending)
        .map(([groupId]) => refresh(groupId)),
    )
    if (handoffs.length === 0 || !(await bootstrap())) return

    // Scheduled 0/250/1000/3000ms checks never close a handoff. Only successful later
    // polling responses count, and two consecutive misses are required.
    for (const [groupId, runId] of handoffs) {
      const current = runsByGroup[groupId]
      if (!current?.handoffPending || current.runId !== runId) continue
      if (Date.now() < (handoffPollEligibleAt.get(groupId) ?? 0)) continue
      const misses = (handoffPollMisses.get(groupId) ?? 0) + 1
      handoffPollMisses.set(groupId, misses)
      if (misses >= HANDOFF_FINALIZE_AFTER_POLLS) finalizeHandoff(groupId, runId)
    }
  }

  async function discover(groupId: string): Promise<void> {
    const existing = runsByGroup[groupId]
    if (!groupId || (existing && !existing.handoffPending) || discoveryInFlight.has(groupId)) return
    discoveryInFlight.add(groupId)
    try {
      const response = await getRequest<any>('/api/v1/ai-invoke/active', { group_id: groupId })
      const payload = response.data ?? {}
      if (payload.active && payload.run_id) {
        const normalized = { ...payload, group_id: payload.group_id ?? groupId }
        if (payload.status === 'finished') trackFinished(normalized)
        else trackStarted(normalized)
      }
    } catch {
      // Best effort: a local start or later lifecycle SSE can still populate the registry.
    } finally {
      discoveryInFlight.delete(groupId)
    }
  }

  async function bootstrap(): Promise<boolean> {
    // P0008 S1: one shot on app mount / reload — running cards come back from the
    // in-memory registry, paused cards from the DB (they survive a server restart).
    if (bootstrapInFlight) return false
    bootstrapInFlight = true
    let reconciled = false
    // Snapshot BEFORE the request: only paused cards that predate this bootstrap are
    // stale-sweep candidates — a pause that lands while the response is in flight
    // must not be wiped by an answer that predates it.
    const sweepCandidates = new Set(
      Object.entries(runsByGroup)
        .filter(([, entry]) => entry.phase === 'paused')
        .map(([groupId]) => groupId),
    )
    try {
      const response = await getRequest<any>('/api/v1/ai-invoke/active-all')
      const payload = response.data ?? {}
      const runs: Record<string, any>[] = Array.isArray(payload.runs) ? payload.runs : []
      const paused: Record<string, any>[] = Array.isArray(payload.paused) ? payload.paused : []
      for (const run of runs) {
        if (!run?.run_id || !run?.group_id) continue
        if (run.status === 'finished') trackFinished(run)
        else trackStarted(run)
      }
      const pausedGroups = new Set<string>()
      for (const row of paused) {
        const groupId = String(row?.group_id ?? '')
        if (!groupId) continue
        pausedGroups.add(groupId)
        const existing = runsByGroup[groupId]
        // A live/finished card for the same group outranks the paused snapshot
        // (the row may simply not be consumed yet while a resumed run reports in).
        if (existing && existing.phase !== 'paused') continue
        runsByGroup[groupId] = pausedEntry(row, existing)
      }
      // Paused cards the server no longer knows are stale (resumed elsewhere,
      // chain ended) — the re-bootstrap after resume_conflict relies on this.
      for (const groupId of sweepCandidates) {
        if (runsByGroup[groupId]?.phase === 'paused' && !pausedGroups.has(groupId)) {
          delete runsByGroup[groupId]
        }
      }
      reconciled = true
    } catch {
      // Best effort: SSE + per-group discovery still populate the list.
    } finally {
      bootstrapInFlight = false
      bootstrapPending.value = false
    }
    return reconciled
  }

  async function cancel(groupId: string): Promise<void> {
    const run = runsByGroup[groupId]
    if (!run || !ACTIVE_PHASES.includes(run.phase) || run.handoffPending || run.cancelling) return
    const response = await postRequest<any>(
      `/api/v1/ai-invoke/${encodeURIComponent(run.runId)}/cancel`,
      {},
    )
    const payload = response.data ?? {}
    if (payload.status === 'finished') {
      // A natural finish can win the race with cancellation. Re-fetch the run so
      // the result card keeps the complete finished payload instead of the terse
      // cancel response, then fall back to that response if recovery was partial.
      await refresh(groupId)
      const recovered = runsByGroup[groupId]
      if (!recovered || recovered.runId !== run.runId || recovered.phase !== 'finished') {
        trackFinished({ ...payload, run_id: payload.run_id ?? run.runId, group_id: payload.group_id ?? groupId })
      }
    } else {
      run.cancelling = true
    }
  }

  async function pause(groupId: string): Promise<void> {
    // Boundary pause (P0008 S4) — continuous runs only; the UI never renders the
    // button for single runs (the server 422s as defense in depth).
    const run = runsByGroup[groupId]
    if (!run || run.phase !== 'running' || run.handoffPending || run.mode !== 'continuous' || run.cancelling) return
    const response = await postRequest<any>(
      `/api/v1/ai-invoke/${encodeURIComponent(run.runId)}/pause`,
      {},
    )
    if ((response.data ?? {}).status === 'pause_requested' && run.phase === 'running') {
      run.phase = 'pause_requested'
    }
  }

  async function resume(groupId: string): Promise<void> {
    const run = runsByGroup[groupId]
    if (!run || run.phase !== 'paused') return
    try {
      const response = await postRequest<any>('/api/v1/ai-invoke/resume', { group_id: groupId })
      const payload = response.data ?? {}
      if (payload.run_id) {
        trackStarted({ ...payload, group_id: payload.group_id ?? groupId })
      }
    } catch (error: any) {
      const status = error?.response?.status
      const code = error?.response?.data?.code
      if (status === 409 && code === 'run_already_active' && error?.response?.data?.run_id) {
        // Already the desired state (P0008 failure 1): adopt the live run as this card.
        trackStarted({
          run_id: error.response.data.run_id,
          group_id: groupId,
          mode: 'continuous',
          doc_ref: run.docRef,
        })
        void refresh(groupId)
        return
      }
      if (status === 409 && (code === 'resume_conflict' || code === 'nothing_to_resume')) {
        // These two codes mean the stored pause no longer exists or has no work left.
        // A launch/advance failure is different: the server restored the row and the UI
        // must surface the rejection instead of deleting and immediately recreating it.
        delete runsByGroup[groupId]
        void bootstrap()
        return
      }
      throw error
    }
  }

  async function releasePaused(groupId: string): Promise<void> {
    // Group-keyed explicit cancel/release of a PAUSED CHAIN row (0459 T0007) --
    // deliberately a separate call from cancel() above, which addresses a live run_id
    // and never touches this DELETE endpoint.
    const run = runsByGroup[groupId]
    if (!run || run.phase !== 'paused') return
    try {
      const response = await deleteRequest<any>(
        `/api/v1/ai-invoke/paused/${encodeURIComponent(groupId)}`,
      )
      const payload = response.data ?? {}
      // Only the server confirming released/already_released counts as success -- an
      // unexpected 2xx shape with neither flag set must not disappear the card either.
      if (payload.released || payload.already_released) {
        delete runsByGroup[groupId]
        schedulePersist()
      }
    } catch (error: any) {
      const status = error?.response?.status
      if (status === 409) {
        // Another session already resumed this chain, or a valid lease is held --
        // never guess: reconcile with the server's authoritative state so the card
        // either becomes the running card another session started, or is restored as
        // still paused. The card is deliberately NOT deleted optimistically here.
        void bootstrap()
      }
      throw error
    }
  }

  function dismiss(groupId: string): void {
    const run = runsByGroup[groupId]
    if (run && !ACTIVE_PHASES.includes(run.phase) && run.phase !== 'paused') {
      delete runsByGroup[groupId]
      schedulePersist()
    }
  }

  function dismissAllFinished(): void {
    // Bulk counterpart to dismiss(), for when a 30-minute TTL has stacked up results.
    // Same guard: only finished/lost cards go, running and paused ones stay put.
    let removed = false
    for (const [groupId, run] of Object.entries(runsByGroup)) {
      if (isFinishedCard(run)) {
        delete runsByGroup[groupId]
        removed = true
      }
    }
    if (removed) schedulePersist()
  }

  async function refreshGroupLease(groupId: string): Promise<void> {
    // Single-flight per group: a caller that fires this on every render (e.g. a watcher
    // on groupId) must not pile up overlapping requests (T0004 task 5 note).
    if (!groupId || leaseFetchInFlight.has(groupId)) return
    leaseFetchInFlight.add(groupId)
    const generation = (leaseFetchGeneration.get(groupId) ?? 0) + 1
    leaseFetchGeneration.set(groupId, generation)
    try {
      const project = groupId.split('.', 1)[0]
      const response = await getRequest<any>('/api/v1/ai-invoke/leases', { project })
      // A newer call for this group already landed (or superseded this one) — a stale
      // response must never overwrite a fresher verdict (T0004 task 5 note, generation check).
      if (leaseFetchGeneration.get(groupId) !== generation) return
      const items: Record<string, any>[] = Array.isArray(response.data?.items) ? response.data.items : []
      const mine = items.find(item => item?.group_id === groupId)
      if (mine) groupLeaseLive[groupId] = Boolean(mine.run_live)
      else delete groupLeaseLive[groupId]
    } catch {
      // Best effort: keep whatever verdict was already known rather than guessing.
    } finally {
      leaseFetchInFlight.delete(groupId)
    }
  }

  async function releaseGroupLease(groupId: string): Promise<void> {
    await postRequest<any>(`/api/v1/ai-invoke/leases/${encodeURIComponent(groupId)}/release`, {})
    delete groupLeaseLive[groupId]
    void refreshGroupLease(groupId)
  }

  function isGroupLeaseLocked(groupId: string | null | undefined): boolean {
    if (!groupId) return false
    return groupId in groupLeaseLive
  }

  function isGroupLeaseOrphaned(groupId: string | null | undefined): boolean {
    if (!groupId) return false
    return groupLeaseLive[groupId] === false
  }

  function isGroupRunning(groupId: string | null | undefined): boolean {
    if (!groupId) return false
    const phase = runsByGroup[groupId]?.phase
    if (phase != null && ACTIVE_PHASES.includes(phase)) return true
    // 0401 NR0003 SS3 cause 3: a 'lost' (or never-tracked) card reads as "not busy" from
    // the run registry alone, but the group can still sit under a lease -- live in
    // another process, or orphaned. Either way the server will 423 a mutation, so the
    // button must already read as locked.
    return isGroupLeaseLocked(groupId)
  }

  function isGroupInlineVisible(groupId: string | null | undefined): boolean {
    if (!groupId) return false
    const phase = runsByGroup[groupId]?.phase
    // MainPanel and AiInvokeInline share this explicit surface contract.
    return phase === 'running' || phase === 'pause_requested'
  }

  function elapsedMsFor(groupId: string): number {
    const run = runsByGroup[groupId]
    if (!run) return 0
    if (!ACTIVE_PHASES.includes(run.phase) || !run.startedAt) return run.elapsedMs
    const startedAtMs = Date.parse(run.startedAt)
    return Number.isFinite(startedAtMs)
      ? Math.max(run.elapsedMs, now.value - startedAtMs)
      : run.elapsedMs
  }

  function trackQuestionRegistered(docId: string): void {
    // L0009 §2.7: correlate a worker-registered Q to its group's card; groups without
    // a card are ignored on purpose — the document panel owns that path.
    const groupId = groupIdFromDocId(docId)
    if (!groupId) return
    const run = runsByGroup[groupId]
    if (!run) return
    if (!run.pendingQDocIds.includes(docId)) run.pendingQDocIds.push(docId)
  }

  function trackQuestionAnswered(docId: string, hasUnanswered = false): void {
    const groupId = groupIdFromDocId(docId)
    if (!groupId) return
    const run = runsByGroup[groupId]
    if (!run || hasUnanswered) return
    run.pendingQDocIds = run.pendingQDocIds.filter(id => id !== docId)
  }

  function sweepFinishedCards(): void {
    // L0009 §2.6 / 0290 NR0003 §5.5, now read through the user's own retention (L0003 §2-3):
    // finished+lost cards decay at the chosen TTL and, beyond the slot count for that
    // choice, the oldest ones go early so a long retention cannot turn the popover into an
    // unbounded run log. Manual dismiss stays immediate; paused and handoff cards are not
    // finished cards, so neither rule reaches them at any setting.
    const ttlMs = retentionTtlMs.value
    const slots = cardSlotsFor(retentionMinutes.value)
    let removed = false
    const survivors: Array<[string, AiInvokeRunEntry]> = []
    for (const [groupId, run] of Object.entries(runsByGroup)) {
      if (!isFinishedCard(run)) continue
      if (isExpired(run.finishedAtMs as number, now.value, ttlMs)) {
        delete runsByGroup[groupId]
        removed = true
      } else {
        survivors.push([groupId, run])
      }
    }
    if (survivors.length > slots) {
      survivors.sort((a, b) => (a[1].finishedAtMs as number) - (b[1].finishedAtMs as number))
      for (const [groupId] of survivors.slice(0, survivors.length - slots)) {
        delete runsByGroup[groupId]
        removed = true
      }
    }
    if (removed) schedulePersist()
  }

  // 0452 L0003 §2-5: adoption is one assignment followed by ONE immediate sweep. Waiting
  // for the next 1s tick would mean somebody who switched to "disappears immediately" sees
  // the list they just emptied for another second. `mirror` is false for a value that came
  // FROM the mirror (another tab's storage event) — rewriting it there would be a no-op at
  // best and an echo at worst.
  function adoptRetention(candidateMinutes: unknown, generation: number, mirror: boolean): void {
    if (generation < retentionAdoptedGeneration) return
    retentionAdoptedGeneration = generation
    const resolved = parseRetentionMinutes(candidateMinutes)
    retentionMinutes.value = resolved
    if (mirror) writeRetentionMirror(resolved)
    sweepFinishedCards()
    flushPersist()
  }

  // A failed lookup must not destroy a value that is already in force (L0003 §4-4 branch 3):
  // it only fills in for a store that has never adopted anything, mirror first, then 30.
  function fallbackRetention(): void {
    if (retentionAdoptedGeneration > 0) return
    retentionMinutes.value = readRetentionMirror() ?? RETENTION_DEFAULT_MINUTES
  }

  async function refreshRetentionSetting(): Promise<void> {
    if (retentionFetchInFlight) return
    retentionFetchInFlight = true
    retentionGeneration += 1
    const generation = retentionGeneration
    try {
      const response = await getRequest<UiSettingsResponse>(UI_SETTINGS_PATH)
      adoptRetention(retentionFromResponse(response.data), generation, true)
    } catch {
      fallbackRetention()
    } finally {
      retentionFetchInFlight = false
    }
  }

  // Exactly this key, and nothing else in localStorage. The account screen writes the
  // mirror only after the server has accepted the value, so an event here is already a
  // saved setting — no re-fetch, adopt and sweep.
  function onRetentionStorage(event: StorageEvent): void {
    if (event.key !== RETENTION_MIRROR_KEY) return
    retentionGeneration += 1
    adoptRetention(event.newValue, retentionGeneration, false)
  }

  function onInvokeEvent(event: Event): void {
    applySse((event as CustomEvent<InvokeSseDetail>).detail)
  }

  function onQRegistered(event: Event): void {
    const docId = (event as CustomEvent<{ doc_id?: string }>).detail?.doc_id
    if (docId) trackQuestionRegistered(String(docId))
  }

  function onQAnswered(event: Event): void {
    const detail = (event as CustomEvent<{
      doc_id?: string
      unanswered_count?: number
    }>).detail
    if (detail?.doc_id) {
      trackQuestionAnswered(String(detail.doc_id), Number(detail.unanswered_count ?? 0) > 0)
    }
  }

  function onRecoverySignal(): void {
    void refreshAllRunning()
    // Coming back online or back to the tab is also when a setting saved elsewhere (a tab
    // that was closed, another machine) has to be picked up (L0003 §2-5 row 3).
    void refreshRetentionSetting()
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === 'visible') onRecoverySignal()
  }

  function clockTick(): void {
    now.value = Date.now()
    sweepFinishedCards()
    flushPersist()
    if (now.value - lastPollAt >= POLL_INTERVAL_MS) {
      lastPollAt = now.value
      void refreshAllRunning()
    }
  }

  let clockTimer: ReturnType<typeof setInterval> | null = null
  if (typeof window !== 'undefined') {
    window.addEventListener('fg:ai_invoke', onInvokeEvent)
    window.addEventListener('fg:q_registered', onQRegistered)
    window.addEventListener('fg:q_answered', onQAnswered)
    window.addEventListener('fg:open_docs_refresh', onRecoverySignal)
    window.addEventListener('online', onRecoverySignal)
    window.addEventListener('storage', onRetentionStorage)
    document.addEventListener('visibilitychange', onVisibilityChange)
    clockTimer = setInterval(clockTick, CLOCK_INTERVAL_MS)
  }

  onScopeDispose(() => {
    if (typeof window !== 'undefined') {
      window.removeEventListener('fg:ai_invoke', onInvokeEvent)
      window.removeEventListener('fg:q_registered', onQRegistered)
      window.removeEventListener('fg:q_answered', onQAnswered)
      window.removeEventListener('fg:open_docs_refresh', onRecoverySignal)
      window.removeEventListener('online', onRecoverySignal)
      window.removeEventListener('storage', onRetentionStorage)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
    if (clockTimer) clearInterval(clockTimer)
    for (const groupId of handoffAdoptionTimers.keys()) clearHandoffTracking(groupId)
    flushPersist()
  })

  return {
    runsByGroup,
    bootstrapPending,
    // 1s clock, exposed so a surface can age its own view of an entry (the inline banner
    // uses it for INLINE_RESULT_WINDOW_MS) without running a second timer.
    now,
    // 0452: the retention in force, and the window the document-screen banner may use.
    // The banner is capped at 60s even when cards never expire, and it disappears with the
    // card itself at 0 (L0003 §2-6).
    retentionMinutes,
    retentionTtlMs,
    inlineResultWindowMs: computed(() => Math.min(INLINE_RESULT_WINDOW_MS, retentionTtlMs.value)),
    refreshRetentionSetting,
    activeCount: computed(() => Object.values(runsByGroup).filter(run => ACTIVE_PHASES.includes(run.phase)).length),
    awaitingQCount: computed(() => Object.values(runsByGroup).filter(isAwaitingQ).length),
    pausedCount: computed(() => Object.values(runsByGroup).filter(run => run.phase === 'paused').length),
    // Alive exactly as long as the card is (0294 B0001) — the sweep, a dismiss or an
    // acknowledged read drops the entry and these fall back on their own, so the chip
    // needs no expiry timer of its own.
    finishedCount: computed(() => Object.values(runsByGroup).filter(isFinishedCard).length),
    finishedAlertCount: computed(() => Object.values(runsByGroup).filter(isFinishedAlert).length),
    trackStarted,
    trackProviderSwitched,
    trackFinished,
    trackQuestionRegistered,
    trackQuestionAnswered,
    markLost,
    applySse,
    bootstrap,
    discover,
    refresh,
    refreshAllRunning,
    cancel,
    pause,
    resume,
    releasePaused,
    dismiss,
    dismissAllFinished,
    sweepFinishedCards,
    adoptRetention,
    isGroupRunning,
    isGroupInlineVisible,
    isGroupLeaseLocked,
    isGroupLeaseOrphaned,
    refreshGroupLease,
    releaseGroupLease,
    elapsedMsFor,
  }
})
