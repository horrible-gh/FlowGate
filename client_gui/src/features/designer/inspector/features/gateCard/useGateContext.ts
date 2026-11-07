// D0007 §6: settings, run and gate form one read context, shared across Inspector tabs.
import { normalizeApiError } from '@shared/apiErrors'
import { randomUuid } from '@shared/utils/uuid'
import { computed, effectScope, hasInjectionContext, inject, nextTick, onScopeDispose, ref, shallowRef, watch, type EffectScope } from 'vue'
import { documentErrorOf, runCurrentRead, runGateReceiptsRead, runGateDecide, settingsNodeRead, settingsNodeWrite, type RunCurrent, type RunGateDecision, type RunGateIntent, type SettingsContext, type SettingsNode } from '@/features/document'
import { INSPECTOR_SELECTION } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorEvents } from '../../useInspectorResource'

export const GATE_RUN_EVENTS = ['graph_run_status_changed', 'graph_node_state_changed', 'gate_publish_job_changed', 'git_status_changed'] as const
export const gateRunEventMatches = (frame: { groupId: string | null }, scope: { flowId: string }) => frame.groupId === scope.flowId
export const gateOf = (target: InspectorTarget | null) => target?.nodeType === 'gate' && !target.nodeId.startsWith('tmp_') ? `${target.flowId}\n${target.nodeId}` : null

type Request = { runId: string; decision: RunGateDecision; comment?: string; intent: RunGateIntent }

// flowgate.v02test.0002 T-b (0006-T §2): the last merge target list the server confirmed (lookup ok / empty), kept in
// memory only (F5 drops it) so a later lookup timeout / failure does not take away a valid choice. It never changes a
// server answer: the live lookup status stays as read, PATCH and the confirm preflight still judge the branch.
export const MERGE_TARGET_CACHE_MS = 5 * 60 * 1000
const CONFIRMED_LOOKUPS = ['ok', 'empty']
// A PATCH of merge_target_branch refused because the target itself is unusable drops the kept list.
const TARGET_INVALID_CODES = ['merge_target_not_found', 'merge_target_invalid', 'target_branch_invalid', 'merge_target_remote_only',
  'merge_target_internal_slot', 'merge_target_same_branch', 'merge_target_owner_mismatch', 'merge_target_incoming_work_base',
  'base_checkout_branch_mismatch']
/** The kept candidates and the context they were confirmed in (every key must match for a use). */
export interface MergeTargetCache {
  readonly projectId: string | null
  readonly flowId: string
  readonly nodeKey: string
  /** `default_target_branch` of the confirming answer: the group's work base. */
  readonly workBase: string | null
  readonly options: readonly string[]
  /** Date.now() when the confirming answer arrived. */
  readonly at: number
}
/** flowgate.v02.0148 T#2: the settings a card lets the Inspector change one value at a time. */
const CHANGEABLE_FIELDS = ['archive', 'merge_mode', 'merge_target_branch', 'push', 'keep_branch']
/** How long a field shows `saved` after the server's answer carried its requested value. */
export const FIELD_SAVED_MS = 2000
/** A field's own save state: queued / on its way, confirmed by the answer, or refused (the shown value is back to the confirmed one). */
export type FieldState = 'saving' | 'saved' | 'failed'
interface QueuedChange { field: string; value: unknown; keeps: number; waiters: Array<(ok: boolean) => void> }
const sameContext = (a: SettingsContext | undefined, b: SettingsContext | undefined) =>
  a === undefined || b === undefined ? a === b : a.run_id === b.run_id && a.graph_revision_no === b.graph_revision_no && a.draft_version === b.draft_version
function refusesTarget(cause: unknown): boolean {
  const normalized = normalizeApiError(cause)
  const reason = typeof normalized.params.reason === 'string' ? normalized.params.reason : null
  return (normalized.code === 'invalid_request') || [normalized.code, reason].some(code => !!code && TARGET_INVALID_CODES.includes(code))
}

function createContext(flowId: string, nodeKey: string, projectId: string | null = null) {
  const key = `${flowId}\n${nodeKey}`
  const run = shallowRef<RunCurrent | null>(null)
  const settings = shallowRef<SettingsNode | null>(null)
  const pending = shallowRef<Readonly<Record<string, unknown>>>({})
  const status = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = shallowRef<unknown>(null)
  const failure = shallowRef<{ field: string; error: unknown } | null>(null)
  const busy = ref<RunGateDecision | null>(null)
  const decisionError = shallowRef<unknown>(null)
  /** A refresh_merge_targets read is asked for or in flight (the candidate re-read, not a lock). */
  const refreshing = ref(false)
  const targetCache = shallowRef<MergeTargetCache | null>(null)
  /** Per field: `saving` while queued / in flight, `saved` for FIELD_SAVED_MS after a confirming answer, `failed` until the next input. */
  const fieldStates = shallowRef<Readonly<Record<string, FieldState>>>({})
  /** Per field: the refusal of its last write (the value that was refused is kept for a retry). */
  const failures = shallowRef<Readonly<Record<string, unknown>>>({})
  const failedValues = new Map<string, unknown>()
  const savedTimers = new Map<string, ReturnType<typeof setTimeout>>()
  // One serial writer: a card's settings are versioned, so PATCHes go out one at a time in the order of the input; a value
  // typed again for a field still waiting replaces the waiting one (latest wins).
  const queue: QueuedChange[] = []
  let draining: Promise<void> | null = null
  /** Date.now() when the merge target views of `settings` were read (a GET or a full PATCH answer); a carried-over view keeps it. */
  let viewAt = 0
  /** The right (`keeps`) of the request that read the views of `settings`: only views read in the current targetEpoch may stand in for an answer. */
  let viewEpoch = NO_RIGHTS
  const node = computed(() => run.value?.nodes.find(n => n.nodeKey === nodeKey) ?? null)
  const gate = computed(() => node.value?.gate ?? null)
  let reading: Promise<boolean> | null = null
  let epoch = 0
  let request: Request | null = null
  let replaying: Promise<boolean> | null = null
  // A read started while a write / decision was answered is dropped; the writer reads once more afterwards.
  let dropped = false
  // The recovery read after the queue (flowgate.v02.0148 T#2 rework): it runs beside the queue, never inside `draining`, so a
  // change typed while a slow GET is still on its way is written at once.
  let recovering: Promise<void> | null = null
  let recoverAgain = false
  let recoverKeeps = NO_RIGHTS
  let cacheTimer: ReturnType<typeof setTimeout> | null = null
  // 0006-T §2 / §3.6: bumped when the Inspector leaves this card. An answer to a GET / PATCH sent before that may still
  // set the values it carries, but it can no longer keep candidates: only a request sent after the return can.
  let targetEpoch = 0
  // Review rej_01M6QXCBVZYSAGRV: the Inspector parts that show this card now (CheckSettings in 설정, GateSlots in 문서).
  let consumers = 0
  // Review rej_01M6R14HCES8QJJ5: the Inspector's selection of the card outlives its gate parts - the Q&A and attachments
  // tabs of the same card mount none. While no part is mounted but the Frame still shows this card, `selectedHold`
  // watches that selection and leaves the card when it moves (another card or kind, nothing selected, the Frame closed).
  let selectedHold: EffectScope | null = null
  /** The Inspector selects this card now: a part of it is mounted, or the Frame's selection is still this card. */
  const shown = () => consumers > 0 || selectedHold !== null
  /** The right to keep candidates for a request sent now: the current targetEpoch, or none once the card was left. */
  const rights = () => (shown() ? targetEpoch : NO_RIGHTS)
  function stopSelectedHold() { selectedHold?.stop(); selectedHold = null }
  function acquire() { consumers++; stopSelectedHold() }
  /** `selected` is the Frame's selection the releasing part lived in (null outside a Frame: the part alone shows the card). */
  function release(selected: (() => InspectorTarget | null) | null = null) {
    consumers = Math.max(0, consumers - 1)
    // A tab change unmounts the part and mounts the next tab's parts in one render. After that render the card is kept
    // while a part shows it or the Frame still selects it (설정 -> Q&A / 첨부 / 문서); otherwise it was left.
    if (consumers === 0) void nextTick(() => {
      if (consumers > 0 || selectedHold) return
      if (selected && gateOf(selected()) === key) holdWhileSelected(selected)
      else leave()
    })
  }
  function holdWhileSelected(selected: () => InspectorTarget | null) {
    const scope = effectScope(true)
    selectedHold = scope
    scope.run(() => watch(() => gateOf(selected()) === key, still => {
      if (still) return
      stopSelectedHold()
      if (consumers === 0) leave()
    }))
  }
  function dropTargets() {
    if (cacheTimer !== null) { clearTimeout(cacheTimer); cacheTimer = null }
    targetCache.value = null
  }
  /** The Inspector left this card: drop the kept candidates and take the right to keep them from requests in flight. */
  function leave() {
    targetEpoch++
    dropTargets()
  }
  /** Keeps / replaces / drops the confirmed candidates by one current answer (a GET of this epoch or a PATCH). */
  function noteTargets(saved: SettingsNode) {
    const state = saved.mergeTargetState
    if (!state) return
    const workBase = saved.defaultTargetBranch ?? null
    if (CONFIRMED_LOOKUPS.includes(state.status)) {
      dropTargets()
      targetCache.value = { projectId, flowId, nodeKey, workBase, options: [...(saved.mergeTargetOptions ?? [])], at: Date.now() }
      cacheTimer = setTimeout(dropTargets, MERGE_TARGET_CACHE_MS)
      return
    }
    // Git itself refused (unavailable, base checkout moved) or another work base answered: nothing kept fits any more.
    if (state.status === 'git_unavailable' || (workBase !== null && targetCache.value && targetCache.value.workBase !== workBase)) dropTargets()
  }
  /** The kept candidates usable for the current answer, or null (none, expired, or any context key differs). */
  function cachedTargets(currentProjectId: string | null = projectId): readonly string[] | null {
    const kept = targetCache.value
    const saved = settings.value
    if (!kept || !saved) return null
    if (Date.now() - kept.at >= MERGE_TARGET_CACHE_MS) return null
    if (kept.projectId !== currentProjectId || kept.flowId !== flowId || kept.nodeKey !== nodeKey) return null
    if (kept.workBase !== (saved.defaultTargetBranch ?? null)) return null
    return kept.options
  }
  /**
   * `keeps` is the right to keep candidates the read carries: by default the one of this moment; a follow-up read of a
   * write / decision carries the right of that write / decision (sent before a move, it keeps nothing).
   */
  function reload(refreshMergeTargets = false, keeps: number = rights()): Promise<boolean> {
    return read(refreshMergeTargets, keeps, false)
  }
  /** `own`: the read of the writer itself (the only one that may land while the queue is still being written). */
  async function read(refreshMergeTargets: boolean, keeps: number, own: boolean): Promise<boolean> {
    if (reading) {
      if (!refreshMergeTargets) return reading
      refreshing.value = true
      await reading
      return read(true, keeps, own)
    }
    const at = epoch
    const before = status.value
    // flowgate.v02.0148 T#2: stale-while-revalidate - only the first read (nothing confirmed yet) puts the context in 'loading';
    // a later read keeps the controls usable while it is on its way.
    if (status.value !== 'ready') status.value = 'loading'
    if (refreshMergeTargets) refreshing.value = true
    reading = (async () => {
      try {
        const [current, saved] = await Promise.allSettled([runCurrentRead(flowId).then(current => runGateReceiptsRead(current, nodeKey)), settingsNodeRead(flowId, nodeKey, refreshMergeTargets)])
        if (at !== epoch || (draining !== null && !own)) {
          // Stale (a write / decision answered meanwhile, or the queue is still writing): neither the values nor the kept candidates change, and the
          // read does not leave the controls in 'loading'.
          dropped = true
          if (status.value === 'loading') status.value = before
          return false
        }
        // Keep each confirmed read even when the other request fails; actions stay locked.
        if (current.status === 'fulfilled') run.value = current.value
        // A read sent before the Inspector left this card keeps no candidates (sent before the switch).
        if (saved.status === 'fulfilled') { settings.value = saved.value; if (saved.value.mergeTargetState !== undefined) { viewAt = Date.now(); viewEpoch = keeps }; if (keeps === targetEpoch) noteTargets(saved.value) }
        if (saved.status === 'rejected') throw saved.reason
        if (current.status === 'rejected') throw current.reason
        error.value = null
        status.value = 'ready'
        return true
      } catch (cause) {
        if (at === epoch) { error.value = cause; status.value = 'error' }
        return false
      } finally {
        reading = null
        if (refreshMergeTargets) refreshing.value = false
      }
    })()
    return reading
  }
  async function send(keeps: number): Promise<boolean> {
    if (!request) return false
    const saved = request
    const answer = await runGateDecide(saved.runId, nodeKey, saved.decision, saved.comment, saved.intent)
    epoch++
    run.value = answer
    request = null
    decisionError.value = null
    // A successful response is authoritative; settings are refreshed before another action.
    if (reading) await reading
    dropped = false
    await reload(false, keeps)
    return true
  }
  async function replay(): Promise<boolean> {
    if (!request) return false
    if (replaying) return replaying
    const keeps = rights()
    replaying = (async () => {
      busy.value = request!.decision
      try {
        // GET precedes every retransmission, also after an SSE reconnect.
        if (!await reload(false, keeps)) return false
        return await send(keeps)
      } catch (cause) {
        decisionError.value = cause
        if (documentErrorOf(cause).kind !== 'network') request = null
        return false
      } finally { busy.value = null; replaying = null }
    })()
    return replaying
  }
  async function decide(decision: RunGateDecision, comment?: string): Promise<boolean> {
    if (decision !== 'confirm' || busy.value || Object.keys(pending.value).length) return false
    if (request) return replay()
    if (status.value !== 'ready' || !gate.value?.can_decide || !node.value?.attemptId || settings.value?.settingsVersion === undefined) return false
    request = { runId: run.value!.runId, decision, comment, intent: { request_id: randomUuid(), expected_attempt_id: node.value.attemptId, expected_settings_version: settings.value.settingsVersion } }
    const keeps = rights()
    busy.value = decision
    decisionError.value = null
    try { return await send(keeps) }
    catch (cause) {
      decisionError.value = cause
      if (documentErrorOf(cause).kind === 'network') {
        busy.value = null
        return await replay()
      }
      request = null
      await reload(false, keeps)
      return false
    } finally { busy.value = null }
  }
  /** The earlier merge target views can stand in for a PATCH answer that leaves them out (flowgate.v02.0148 T#2). */
  function canKeepViews(prev: SettingsNode | null, keeps: number): prev is SettingsNode {
    return !!prev && keeps === targetEpoch && viewEpoch === targetEpoch && prev.mergeTargetOptions !== undefined && prev.defaultTargetBranch !== undefined && !!prev.mergeTargetState
      && Date.now() - viewAt < MERGE_TARGET_CACHE_MS
  }
  /**
   * The merge contract: only an answer marked `merge_targets_kept` takes the earlier options / work base / lookup state over,
   * and only while the same context holds, the Inspector did not leave the card and the views are younger than
   * MERGE_TARGET_CACHE_MS. Any other key of the answer (values, version, lock, context) stays the answer's. Null = not
   * mergeable: the caller reads the full view instead of leaving the candidates empty.
   */
  function mergeKept(prev: SettingsNode | null, saved: SettingsNode, keeps: number): SettingsNode | null {
    if (!canKeepViews(prev, keeps) || !sameContext(prev.context, saved.context)) return null
    const { mergeTargetsKept: _kept, ...rest } = saved
    return { ...rest, mergeTargetOptions: prev.mergeTargetOptions, defaultTargetBranch: prev.defaultTargetBranch, mergeTargetState: prev.mergeTargetState }
  }
  function setFieldState(field: string, state: FieldState | null) {
    const next = { ...fieldStates.value }
    if (state) next[field] = state; else delete next[field]
    fieldStates.value = next
  }
  function setFailure(field: string, cause: unknown | null) {
    const next = { ...failures.value }
    if (cause === null) delete next[field]; else next[field] = cause
    failures.value = next
  }
  function setPending(field: string, value: unknown | undefined) {
    const next = { ...pending.value }
    if (value === undefined) delete next[field]; else next[field] = value
    pending.value = next
  }
  /** One PATCH of the queue; true = saved. The follow-up read is the caller's (`state.again`). */
  async function writeOne(entry: QueuedChange, state: { again: boolean }): Promise<boolean> {
    const { field, value, keeps } = entry
    const prev = settings.value
    epoch++
    try {
      // The answer may leave the merge target views out only when the earlier ones could be carried over.
      const optIn = field !== 'merge_target_branch' && canKeepViews(prev, keeps)
      const saved = await settingsNodeWrite(flowId, nodeKey, field, value, prev?.settingsVersion, undefined, optIn ? { keepMergeTargets: true } : undefined)
      if (saved.mergeTargetsKept) {
        const merged = mergeKept(prev, saved, keeps)
        // Not mergeable any more (context, card left, expired): the values hold, the candidates are read in full right after.
        if (merged) settings.value = merged
        else { settings.value = saved; state.again = true }
      } else {
        settings.value = saved
        // The saved answer is authoritative, also for the candidates it carries - unless the Inspector left the card
        // while it was on its way: then its value still holds, but it keeps no candidates.
        if (saved.mergeTargetState !== undefined) { viewAt = Date.now(); viewEpoch = keeps }
        if (keeps === targetEpoch) noteTargets(saved)
      }
      decisionError.value = null
      setFailure(field, null)
      failedValues.delete(field)
      if (failure.value?.field === field) failure.value = null
      const confirmed = value === null || saved.fields[field]?.value === value
      const waiting = queue.some(q => q.field === field)
      if (!waiting) setFieldState(field, confirmed ? 'saved' : null)
      if (confirmed && !waiting) {
        clearTimeout(savedTimers.get(field))
        savedTimers.set(field, setTimeout(() => { savedTimers.delete(field); if (fieldStates.value[field] === 'saved') setFieldState(field, null) }, FIELD_SAVED_MS))
      }
      return true
    } catch (cause) {
      failure.value = { field, error: cause }
      setFailure(field, cause)
      failedValues.set(field, value)
      // A newer value of this field still waiting is the user's next input: it stays `saving` and is sent.
      setFieldState(field, queue.some(q => q.field === field) ? 'saving' : 'failed')
      // Re-read refused targets/versions; legacy conflicts preserve all saved fields.
      if (normalizeApiError(cause).params.reason !== 'legacy_merge_target_conflict') state.again = true
      // 0006-T §2: a refusal saying the target itself is unusable drops the kept candidates (version / lock / busy keep them).
      if (field === 'merge_target_branch' && refusesTarget(cause)) dropTargets()
      return false
    } finally {
      // Invalidates reads started before the write's answer; failed writes keep the confirmed value.
      epoch++
      if (!queue.some(q => q.field === field)) setPending(field, undefined)
    }
  }
  function drain(): Promise<void> {
    if (draining) return draining
    draining = (async () => {
      const state = { again: false }
      let keeps = rights()
      try {
        while (queue.length) {
          const entry = queue.shift()!
          keeps = entry.keeps
          const ok = await writeOne(entry, state)
          entry.waiters.forEach(done => done(ok))
          // A refused version / lock leaves the next write with a stale version: read before it (the read of the queue itself).
          if (!ok && state.again && queue.length) { state.again = false; if (reading) await reading; await read(false, keeps, true) }
        }
      } finally {
        // The queue ends here, without waiting for any read: a change typed meanwhile starts its own drain at once. What
        // is left to read (a refused write, a read dropped as stale) goes to `recover`, which runs beside the queue.
        // The follow-up carries the right of the last write to keep candidates: a write sent before the Inspector left
        // the card keeps nothing.
        draining = null
        recoverAgain = recoverAgain || state.again
        recoverKeeps = keeps
        recover()
      }
    })()
    return draining
  }
  /**
   * A read dropped as stale (an event's, also one still in flight when the last write was answered) is read once more
   * after the writes. The reads in flight are awaited first: they decide `dropped` only when they land. While the queue
   * is writing again the read waits: the end of that queue comes back here.
   */
  function recover() {
    if (recovering) return
    // The body may finish synchronously (nothing left to read): its `finally` then runs before the assignment below, so
    // the handle is only stored while the body is still pending - a finished promise must never stay in `recovering`.
    let finished = false
    const running = (async () => {
      try {
        for (;;) {
          if (reading) await reading
          if (draining) return
          const again = recoverAgain || dropped
          dropped = false; recoverAgain = false
          if (!again) return
          await read(false, recoverKeeps, true)
        }
      } finally { finished = true; recovering = null }
    })()
    if (!finished) recovering = running
  }
  /**
   * Queues one change. Each field has its own state (`fieldStates`); changes of different fields are written one after the
   * other in input order, a newer value of a field still waiting replaces the waiting one. True = this value (or the one that
   * replaced it) was saved.
   */
  function change(field: string, value: unknown): Promise<boolean> {
    if (!CHANGEABLE_FIELDS.includes(field)) return Promise.resolve(false)
    if (settings.value?.settingsVersion === undefined || status.value !== 'ready' || settings.value?.locked || gate.value?.settings_locked || busy.value || request) return Promise.resolve(false)
    clearTimeout(savedTimers.get(field)); savedTimers.delete(field)
    setFailure(field, null)
    if (failure.value?.field === field) failure.value = null
    setPending(field, value)
    setFieldState(field, 'saving')
    return new Promise<boolean>(resolve => {
      const waiting = queue.find(q => q.field === field)
      if (waiting) { waiting.value = value; waiting.waiters.push(resolve) } else queue.push({ field, value, keeps: rights(), waiters: [resolve] })
      void drain()
    })
  }
  /** Sends again the value a field's write was refused for. */
  function retry(field: string): Promise<boolean> {
    return failedValues.has(field) ? change(field, failedValues.get(field)) : Promise.resolve(false)
  }
  return { run, settings, node, gate, pending, status, error, failure, failures, fieldStates, busy, decisionError, refreshing, targetCache, cachedTargets, dropTargets,
    leave, acquire, release, active: shown, reload, decide, change, retry, replay, hasRequest: () => request !== null }
}
const NO_RIGHTS = -1
const contexts = new Map<string, ReturnType<typeof createContext>>()
/** The shared context of a gate target if one exists, without showing it (a test / diagnostics read: no consumer). */
export function gateContextOf(target: InspectorTarget | null) {
  const key = gateOf(target)
  return key ? contexts.get(key) ?? null : null
}
export function useGateContext(target: () => InspectorTarget | null) {
  const context = computed(() => {
    const key = gateOf(target())
    if (!key) return null
    if (!contexts.has(key)) contexts.set(key, createContext(...key.split('\n') as [string, string], target()?.projectId ?? null))
    return contexts.get(key)!
  })
  // 0006-T §2: this part shows `held`. Switching project / flow / card, or this part being unmounted (another kind of
  // card selected, or another tab of the same card), releases it. When neither a part nor the Frame's selection shows
  // the card any more it is left: the kept candidates are dropped and an answer still on its way (a GET or PATCH sent
  // before, or the follow-up read of such a PATCH) can no longer keep them again. A tab change on the same card is no
  // move (rej_01M6R14HCES8QJJ5).
  const frame = hasInjectionContext() ? inject(INSPECTOR_SELECTION, null) : null
  const selected = frame ? () => frame.target : null
  let held: ReturnType<typeof createContext> | null = null
  watch(context, value => {
    if (held !== value) { value?.acquire(); held?.release(selected); held = value }
    if (value) void (value.hasRequest() ? value.replay() : value.reload())
  }, { immediate: true })
  onScopeDispose(() => { held?.release(selected); held = null })
  watch(() => `${target()?.nodeRun?.status ?? ''}\n${target()?.canvasState ?? ''}`, () => { void context.value?.reload() })
  useInspectorEvents(() => gateOf(target()) ? target() : null, {
    events: GATE_RUN_EVENTS, matches: gateRunEventMatches,
    changed: () => { const c = context.value; if (c) void (c.hasRequest() ? c.replay() : c.reload()) },
  })
  return context
}
