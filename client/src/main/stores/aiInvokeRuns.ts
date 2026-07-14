import { computed, onScopeDispose, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { getRequest, postRequest } from '@shared/api'

export type AiInvokePhase = 'running' | 'finished' | 'lost'

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
}

type InvokeSseDetail = {
  kind?: 'started' | 'switched' | 'finished'
  payload?: Record<string, any>
}

const POLL_INTERVAL_MS = 5_000
const CLOCK_INTERVAL_MS = 1_000

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
    phase: 'running',
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

export const useAiInvokeRunsStore = defineStore('ai-invoke-runs', () => {
  const runsByGroup = reactive<Record<string, AiInvokeRunEntry>>({})
  const now = ref(Date.now())
  const discoveryInFlight = new Set<string>()
  const refreshingRunIds = new Set<string>()
  let lastPollAt = 0

  function trackStarted(payload: Record<string, any>): void {
    if (!payload?.run_id || !payload?.group_id) return
    const groupId = String(payload.group_id)
    runsByGroup[groupId] = startedEntry(payload, runsByGroup[groupId])
  }

  function trackProviderSwitched(payload: Record<string, any>): void {
    if (!payload?.run_id || !payload?.group_id) return
    const groupId = String(payload.group_id)
    const run = runsByGroup[groupId]
    if (!run || run.runId !== String(payload.run_id) || run.phase !== 'running') return

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
    if (existing && existing.runId !== runId) return

    const base = existing ?? startedEntry(payload)
    const finishedSwitches = normalizeSwitches(payload.fallback_history)
    runsByGroup[groupId] = {
      ...base,
      runId,
      groupId,
      docRef: String(payload.doc_ref ?? base.docRef),
      phase: 'finished',
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
    }
  }

  function markLost(groupId: string, runId?: string): void {
    const run = runsByGroup[groupId]
    if (!run || (runId && run.runId !== runId) || run.phase !== 'running') return
    run.phase = 'lost'
    run.cancelling = false
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
    if (!run || run.phase !== 'running' || refreshingRunIds.has(run.runId)) return
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
        .filter(([, run]) => run.phase === 'running')
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

  async function cancel(groupId: string): Promise<void> {
    const run = runsByGroup[groupId]
    if (!run || run.phase !== 'running' || run.cancelling) return
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

  function dismiss(groupId: string): void {
    const run = runsByGroup[groupId]
    if (run && run.phase !== 'running') delete runsByGroup[groupId]
  }

  function isGroupRunning(groupId: string | null | undefined): boolean {
    return !!groupId && runsByGroup[groupId]?.phase === 'running'
  }

  function elapsedMsFor(groupId: string): number {
    const run = runsByGroup[groupId]
    if (!run) return 0
    if (run.phase !== 'running' || !run.startedAt) return run.elapsedMs
    const startedAtMs = Date.parse(run.startedAt)
    return Number.isFinite(startedAtMs)
      ? Math.max(run.elapsedMs, now.value - startedAtMs)
      : run.elapsedMs
  }

  function onInvokeEvent(event: Event): void {
    applySse((event as CustomEvent<InvokeSseDetail>).detail)
  }

  function onRecoverySignal(): void {
    void refreshAllRunning()
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === 'visible') onRecoverySignal()
  }

  function clockTick(): void {
    now.value = Date.now()
    if (now.value - lastPollAt >= POLL_INTERVAL_MS) {
      lastPollAt = now.value
      void refreshAllRunning()
    }
  }

  let clockTimer: ReturnType<typeof setInterval> | null = null
  if (typeof window !== 'undefined') {
    window.addEventListener('fg:ai_invoke', onInvokeEvent)
    window.addEventListener('fg:open_docs_refresh', onRecoverySignal)
    window.addEventListener('online', onRecoverySignal)
    document.addEventListener('visibilitychange', onVisibilityChange)
    clockTimer = setInterval(clockTick, CLOCK_INTERVAL_MS)
  }

  onScopeDispose(() => {
    if (typeof window !== 'undefined') {
      window.removeEventListener('fg:ai_invoke', onInvokeEvent)
      window.removeEventListener('fg:open_docs_refresh', onRecoverySignal)
      window.removeEventListener('online', onRecoverySignal)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
    if (clockTimer) clearInterval(clockTimer)
  })

  return {
    runsByGroup,
    activeCount: computed(() => Object.values(runsByGroup).filter(run => run.phase === 'running').length),
    trackStarted,
    trackProviderSwitched,
    trackFinished,
    markLost,
    applySse,
    discover,
    refresh,
    refreshAllRunning,
    cancel,
    dismiss,
    isGroupRunning,
    elapsedMsFor,
  }
})
