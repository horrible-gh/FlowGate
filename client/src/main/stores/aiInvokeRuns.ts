import { computed, onScopeDispose, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { getRequest, postRequest } from '@shared/api'

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
  startedAt: string | null
  elapsedMs: number
  providerSwitches: AiInvokeProviderSwitch[]
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
  // Miniplayer additions (group 0252): the awaiting-answer overlay is a DERIVED flag
  // built from pendingQDocIds — never a phase value (D0007 decision 3).
  pendingQDocIds: string[]
  pausedAt: string | null
  finishedAtMs: number | null
}

type InvokeSseDetail = {
  kind?: 'started' | 'switched' | 'finished'
  payload?: Record<string, any>
}

const POLL_INTERVAL_MS = 5_000
const CLOCK_INTERVAL_MS = 1_000
// L0009 §1: finished/cancelled/lost cards leave the list on their own; paused cards never do.
// 0290 NR0003 §5.1: 10s was effectively "gone before it was read" — the header monitor is
// the channel this user actually watches for completions, so a finished card now survives
// long enough to walk away from. Reading the card (문서 열기) or removing it stays instant.
export const FINISHED_CARD_TTL_MS = 30 * 60_000
// The document screen's inline banner shares this registry but not its lifetime: a result
// panel pinned over the document for 30 minutes is nobody's idea of helpful (NR0003 §5.3).
export const INLINE_RESULT_WINDOW_MS = 60_000
// A 30-minute TTL turns the list into a log unless it is capped (NR0003 §5.5).
export const MAX_FINISHED_CARDS = 20
const FINISHED_STORAGE_KEY = 'fg.ai_invoke.finished_cards'

function nullableString(value: unknown): string | null {
  return value == null || value === '' ? null : String(value)
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

function normalizeProvider(payload: Record<string, any>, previous: AiInvokeProvider | null = null): AiInvokeProvider | null {
  if (payload.provider && typeof payload.provider === 'object') {
    return {
      id: nullableString(payload.provider.id ?? payload.provider.provider_id),
      name: nullableString(payload.provider.name ?? payload.provider.provider_name),
    }
  }
  if (payload.provider_id != null || payload.provider_name != null) {
    return {
      id: nullableString(payload.provider_id),
      name: nullableString(payload.provider_name),
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
  const sameRun = previous?.runId === String(payload.run_id ?? '')
  return {
    runId: String(payload.run_id ?? previous?.runId ?? ''),
    groupId: String(payload.group_id ?? previous?.groupId ?? ''),
    docRef: String(payload.doc_ref ?? (sameRun ? previous?.docRef : '') ?? ''),
    phase: payload.status === 'pause_requested' ? 'pause_requested' : 'running',
    mode: payload.mode === 'continuous' ? 'continuous' : (sameRun ? previous?.mode ?? 'single' : 'single'),
    cancelling: payload.status === 'cancelling',
    provider: normalizeProvider(payload, sameRun ? previous?.provider ?? null : null),
    attemptNo: Number(payload.attempt_no ?? (sameRun ? previous?.attemptNo : 1) ?? 1),
    docsTarget: Number(payload.docs_target ?? (sameRun ? previous?.docsTarget : 1) ?? 1),
    docsReachedSoFar: Number(payload.docs_reached_so_far ?? (sameRun ? previous?.docsReachedSoFar : 0) ?? 0),
    startedAt: nullableString(payload.started_at) ?? (sameRun ? previous?.startedAt ?? null : null),
    elapsedMs: Number(payload.elapsed_ms ?? (sameRun ? previous?.elapsedMs : 0) ?? 0),
    providerSwitches: sameRun ? previous?.providerSwitches ?? [] : [],
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
    pendingQDocIds: Array.isArray(payload.pending_q_doc_ids)
      ? stringArray(payload.pending_q_doc_ids)
      : (sameRun ? previous?.pendingQDocIds ?? [] : []),
    pausedAt: sameRun ? previous?.pausedAt ?? null : null,
    finishedAtMs: null,
  }
}

function pausedEntry(payload: Record<string, any>, previous?: AiInvokeRunEntry): AiInvokeRunEntry {
  // A paused chain has no live run (P0008 S5) — the card is keyed by group alone.
  return {
    runId: previous?.runId ?? '',
    groupId: String(payload.group_id ?? previous?.groupId ?? ''),
    docRef: String(payload.doc_ref ?? previous?.docRef ?? ''),
    phase: 'paused',
    mode: 'continuous',
    cancelling: false,
    provider: previous?.provider ?? null,
    attemptNo: previous?.attemptNo ?? 1,
    docsTarget: Number(payload.docs_target ?? previous?.docsTarget ?? 0),
    docsReachedSoFar: Number(payload.docs_reached ?? previous?.docsReachedSoFar ?? 0),
    startedAt: null,
    elapsedMs: previous?.elapsedMs ?? 0,
    providerSwitches: [],
    finishedPayload: null,
    outcome: null,
    docsReached: Number(payload.docs_reached ?? 0),
    reachedDocIds: [],
    endReason: 'user_paused',
    lastMessageReceived: false,
    lastMessage: null,
    sourceDirty: null,
    sourceDirtyFiles: [],
    registerErrors: [],
    toolCallMisses: 0,
    turnLimitExhausted: false,
    oracleMismatch: false,
    pendingQDocIds: stringArray(payload.pending_q_doc_ids),
    pausedAt: nullableString(payload.paused_at),
    finishedAtMs: null,
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

// The one predicate for "a result the user has not dealt with yet". 0294 B0001: every
// surface that summarizes the registry — including the CLOSED header chip — has to count
// these for exactly as long as the card lives, or the completion is the single state that
// is never shown.
export function isFinishedCard(entry: AiInvokeRunEntry): boolean {
  // A user_paused finish is a PAUSED card (P0008 S4) — never a decay/persist candidate.
  return (entry.phase === 'finished' || entry.phase === 'lost')
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
function loadPersistedFinished(): Record<string, AiInvokeRunEntry> {
  if (typeof sessionStorage === 'undefined') return {}
  try {
    const raw = sessionStorage.getItem(FINISHED_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return {}
    const cutoff = Date.now() - FINISHED_CARD_TTL_MS
    const restored: Record<string, AiInvokeRunEntry> = {}
    for (const [groupId, entry] of Object.entries(parsed as Record<string, AiInvokeRunEntry>)) {
      if (entry && isFinishedCard(entry) && (entry.finishedAtMs as number) > cutoff) {
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
  try {
    const snapshot: Record<string, AiInvokeRunEntry> = {}
    for (const [groupId, entry] of Object.entries(runsByGroup)) {
      if (isFinishedCard(entry)) snapshot[groupId] = entry
    }
    if (Object.keys(snapshot).length === 0) sessionStorage.removeItem(FINISHED_STORAGE_KEY)
    else sessionStorage.setItem(FINISHED_STORAGE_KEY, JSON.stringify(snapshot))
  } catch {
    // Quota/private-mode failures are not worth breaking the monitor over.
  }
}

export const useAiInvokeRunsStore = defineStore('ai-invoke-runs', () => {
  const runsByGroup = reactive<Record<string, AiInvokeRunEntry>>(loadPersistedFinished())
  const now = ref(Date.now())
  const discoveryInFlight = new Set<string>()
  const refreshingRunIds = new Set<string>()
  let lastPollAt = 0
  let bootstrapInFlight = false
  let persistDirty = false

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

  function trackStarted(payload: Record<string, any>): void {
    if (!payload?.run_id || !payload?.group_id) return
    const groupId = String(payload.group_id)
    const replacedFinished = runsByGroup[groupId] != null && isFinishedCard(runsByGroup[groupId])
    runsByGroup[groupId] = startedEntry(payload, runsByGroup[groupId])
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

  function trackFinished(payload: Record<string, any>): void {
    if (!payload?.run_id || !payload?.group_id) return
    const groupId = String(payload.group_id)
    const runId = String(payload.run_id)
    const existing = runsByGroup[groupId]
    if (existing && existing.runId !== runId && existing.runId !== '') return

    const base = existing ?? startedEntry(payload)
    // Boundary stop (P0008 S4): a user-paused finish is a PAUSED card with a resume
    // button, never a finished card — and never a TTL-sweep candidate.
    const userPaused = payload.end_reason === 'user_paused'
    const finishedSwitches = normalizeSwitches(payload.fallback_history)
    runsByGroup[groupId] = {
      ...base,
      runId,
      groupId,
      docRef: String(payload.doc_ref ?? base.docRef),
      phase: userPaused ? 'paused' : 'finished',
      cancelling: false,
      provider: normalizeProvider(payload, base.provider),
      attemptNo: Number(payload.attempt_no ?? base.attemptNo),
      docsTarget: Number(payload.docs_target ?? base.docsTarget),
      docsReachedSoFar: Number(payload.docs_reached ?? payload.docs_reached_so_far ?? base.docsReachedSoFar),
      elapsedMs: Number(payload.duration_ms ?? payload.elapsed_ms ?? base.elapsedMs),
      providerSwitches: finishedSwitches.length > 0 ? finishedSwitches : base.providerSwitches,
      finishedPayload: { ...payload },
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
      pausedAt: userPaused ? new Date().toISOString() : base.pausedAt,
      finishedAtMs: userPaused ? null : Date.now(),
    }
    schedulePersist()
  }

  function markLost(groupId: string, runId?: string): void {
    const run = runsByGroup[groupId]
    if (!run || (runId && run.runId !== runId) || !ACTIVE_PHASES.includes(run.phase)) return
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
    await Promise.all(
      Object.entries(runsByGroup)
        .filter(([, run]) => ACTIVE_PHASES.includes(run.phase))
        .map(([groupId]) => refresh(groupId)),
    )
  }

  async function discover(groupId: string): Promise<void> {
    if (!groupId || runsByGroup[groupId] || discoveryInFlight.has(groupId)) return
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

  async function bootstrap(): Promise<void> {
    // P0008 S1: one shot on app mount / reload — running cards come back from the
    // in-memory registry, paused cards from the DB (they survive a server restart).
    if (bootstrapInFlight) return
    bootstrapInFlight = true
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
    } catch {
      // Best effort: SSE + per-group discovery still populate the list.
    } finally {
      bootstrapInFlight = false
    }
  }

  async function cancel(groupId: string): Promise<void> {
    const run = runsByGroup[groupId]
    if (!run || !ACTIVE_PHASES.includes(run.phase) || run.cancelling) return
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
    if (!run || run.phase !== 'running' || run.mode !== 'continuous' || run.cancelling) return
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
        // Already the desired state (P0008 실패 1): adopt the live run as this card.
        trackStarted({
          run_id: error.response.data.run_id,
          group_id: groupId,
          mode: 'continuous',
          doc_ref: run.docRef,
        })
        void refresh(groupId)
        return
      }
      if (status === 409) {
        // resume_conflict / nothing_to_resume (P0008 실패 2): another path already
        // resumed or finished the chain — drop the card and re-sync from the server.
        delete runsByGroup[groupId]
        void bootstrap()
        return
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

  function isGroupRunning(groupId: string | null | undefined): boolean {
    if (!groupId) return false
    const phase = runsByGroup[groupId]?.phase
    return phase != null && ACTIVE_PHASES.includes(phase)
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

  function trackQuestionAnswered(docId: string): void {
    const groupId = groupIdFromDocId(docId)
    if (!groupId) return
    const run = runsByGroup[groupId]
    if (!run) return
    run.pendingQDocIds = run.pendingQDocIds.filter(id => id !== docId)
  }

  function sweepFinishedCards(): void {
    // L0009 §2.6 / 0290 NR0003 §5.5: finished+lost cards decay after FINISHED_CARD_TTL_MS
    // and, beyond MAX_FINISHED_CARDS, the oldest ones go early so a long TTL cannot turn
    // the popover into an unbounded run log. Manual dismiss stays immediate; paused cards
    // never decay by either rule.
    let removed = false
    const survivors: Array<[string, AiInvokeRunEntry]> = []
    for (const [groupId, run] of Object.entries(runsByGroup)) {
      if (!isFinishedCard(run)) continue
      if (now.value - (run.finishedAtMs as number) >= FINISHED_CARD_TTL_MS) {
        delete runsByGroup[groupId]
        removed = true
      } else {
        survivors.push([groupId, run])
      }
    }
    if (survivors.length > MAX_FINISHED_CARDS) {
      survivors.sort((a, b) => (a[1].finishedAtMs as number) - (b[1].finishedAtMs as number))
      for (const [groupId] of survivors.slice(0, survivors.length - MAX_FINISHED_CARDS)) {
        delete runsByGroup[groupId]
        removed = true
      }
    }
    if (removed) schedulePersist()
  }

  function onInvokeEvent(event: Event): void {
    applySse((event as CustomEvent<InvokeSseDetail>).detail)
  }

  function onQRegistered(event: Event): void {
    const docId = (event as CustomEvent<{ doc_id?: string }>).detail?.doc_id
    if (docId) trackQuestionRegistered(String(docId))
  }

  function onQAnswered(event: Event): void {
    const docId = (event as CustomEvent<{ doc_id?: string }>).detail?.doc_id
    if (docId) trackQuestionAnswered(String(docId))
  }

  function onRecoverySignal(): void {
    void refreshAllRunning()
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
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
    if (clockTimer) clearInterval(clockTimer)
    flushPersist()
  })

  return {
    runsByGroup,
    // 1s clock, exposed so a surface can age its own view of an entry (the inline banner
    // uses it for INLINE_RESULT_WINDOW_MS) without running a second timer.
    now,
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
    dismiss,
    dismissAllFinished,
    sweepFinishedCards,
    isGroupRunning,
    elapsedMsFor,
  }
})
