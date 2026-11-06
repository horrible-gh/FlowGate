// D0007 §6: settings, run and gate form one read context, shared across Inspector tabs.
import { normalizeApiError } from '@shared/apiErrors'
import { randomUuid } from '@shared/utils/uuid'
import { computed, effectScope, hasInjectionContext, inject, nextTick, onScopeDispose, ref, shallowRef, watch, type EffectScope } from 'vue'
import { documentErrorOf, runCurrentRead, runGateReceiptsRead, runGateDecide, settingsNodeRead, settingsNodeWrite, type RunCurrent, type RunGateDecision, type RunGateIntent, type SettingsNode } from '@/features/document'
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
  const node = computed(() => run.value?.nodes.find(n => n.nodeKey === nodeKey) ?? null)
  const gate = computed(() => node.value?.gate ?? null)
  let reading: Promise<boolean> | null = null
  let epoch = 0
  let request: Request | null = null
  let replaying: Promise<boolean> | null = null
  // A read started while a write / decision was answered is dropped; the writer reads once more afterwards.
  let dropped = false
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
  async function reload(refreshMergeTargets = false, keeps: number = rights()): Promise<boolean> {
    if (reading) {
      if (!refreshMergeTargets) return reading
      refreshing.value = true
      await reading
      return reload(true, keeps)
    }
    const at = epoch
    const before = status.value
    status.value = 'loading'
    if (refreshMergeTargets) refreshing.value = true
    reading = (async () => {
      try {
        const [current, saved] = await Promise.allSettled([runCurrentRead(flowId).then(current => runGateReceiptsRead(current, nodeKey)), settingsNodeRead(flowId, nodeKey, refreshMergeTargets)])
        if (at !== epoch) {
          // Stale (a write / decision answered meanwhile): neither the values nor the kept candidates change, and the
          // read does not leave the controls in 'loading'.
          dropped = true
          if (status.value === 'loading') status.value = before
          return false
        }
        // Keep each confirmed read even when the other request fails; actions stay locked.
        if (current.status === 'fulfilled') run.value = current.value
        // A read sent before the Inspector left this card keeps no candidates (sent before the switch).
        if (saved.status === 'fulfilled') { settings.value = saved.value; if (keeps === targetEpoch) noteTargets(saved.value) }
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
  async function change(field: string, value: unknown): Promise<boolean> {
    if (!['archive', 'merge_mode', 'merge_target_branch', 'push', 'keep_branch'].includes(field)) return false
    if (settings.value?.settingsVersion === undefined || status.value !== 'ready' || settings.value?.locked || gate.value?.settings_locked || busy.value || request || Object.keys(pending.value).length) return false
    epoch++
    const keeps = rights()
    let refreshAfterFailure = false
    pending.value = { [field]: value }
    failure.value = null
    try {
      const saved = await settingsNodeWrite(flowId, nodeKey, field, value, settings.value?.settingsVersion)
      settings.value = saved
      // The saved answer is authoritative, also for the candidates it carries - unless the Inspector left the card
      // while it was on its way: then its value still holds, but it keeps no candidates.
      if (keeps === targetEpoch) noteTargets(saved)
      decisionError.value = null
      return true
    } catch (cause) {
      failure.value = { field, error: cause }
      refreshAfterFailure = normalizeApiError(cause).params.reason !== 'legacy_merge_target_conflict'
      // 0006-T §2: a refusal saying the target itself is unusable drops the kept candidates (version / lock / busy keep them).
      if (field === 'merge_target_branch' && refusesTarget(cause)) dropTargets()
      return false
    } finally {
      pending.value = {}
      // Invalidates reads started before the write's answer; failed writes keep the confirmed value.
      epoch++
      if (reading) await reading
      // A successful PATCH is authoritative. Re-read refused targets/versions; legacy conflicts preserve all saved fields.
      // A read dropped as stale meanwhile (an event's) is read once more after the write. It carries the write's right
      // to keep candidates: a write sent before the Inspector left the card keeps nothing through its follow-up read.
      const again = refreshAfterFailure || dropped
      dropped = false
      if (again) await reload(false, keeps)
    }
  }
  return { run, settings, node, gate, pending, status, error, failure, busy, decisionError, refreshing, targetCache, cachedTargets, dropTargets,
    leave, acquire, release, active: shown, reload, decide, change, replay, hasRequest: () => request !== null }
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
