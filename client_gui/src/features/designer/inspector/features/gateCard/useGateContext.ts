// D0007 §6: settings, run and gate form one read context, shared across Inspector tabs.
import { normalizeApiError } from '@shared/apiErrors'
import { randomUuid } from '@shared/utils/uuid'
import { computed, ref, shallowRef, watch } from 'vue'
import { documentErrorOf, runCurrentRead, runGateReceiptsRead, runGateDecide, settingsNodeRead, settingsNodeWrite, type RunCurrent, type RunGateDecision, type RunGateIntent, type SettingsNode } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorEvents } from '../../useInspectorResource'

export const GATE_RUN_EVENTS = ['graph_run_status_changed', 'graph_node_state_changed', 'gate_publish_job_changed', 'git_status_changed'] as const
export const gateRunEventMatches = (frame: { groupId: string | null }, scope: { flowId: string }) => frame.groupId === scope.flowId
export const gateOf = (target: InspectorTarget | null) => target?.nodeType === 'gate' && !target.nodeId.startsWith('tmp_') ? `${target.flowId}\n${target.nodeId}` : null

type Request = { runId: string; decision: RunGateDecision; comment?: string; intent: RunGateIntent }
function createContext(flowId: string, nodeKey: string) {
  const run = shallowRef<RunCurrent | null>(null)
  const settings = shallowRef<SettingsNode | null>(null)
  const pending = shallowRef<Readonly<Record<string, unknown>>>({})
  const status = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = shallowRef<unknown>(null)
  const failure = shallowRef<{ field: string; error: unknown } | null>(null)
  const busy = ref<RunGateDecision | null>(null)
  const decisionError = shallowRef<unknown>(null)
  const node = computed(() => run.value?.nodes.find(n => n.nodeKey === nodeKey) ?? null)
  const gate = computed(() => node.value?.gate ?? null)
  let reading: Promise<boolean> | null = null
  let epoch = 0
  let request: Request | null = null
  let replaying: Promise<boolean> | null = null
  async function reload(refreshMergeTargets = false): Promise<boolean> {
    if (reading) {
      if (!refreshMergeTargets) return reading
      await reading
      return reload(true)
    }
    const at = epoch
    status.value = 'loading'
    reading = (async () => {
      try {
        const [current, saved] = await Promise.allSettled([runCurrentRead(flowId).then(current => runGateReceiptsRead(current, nodeKey)), settingsNodeRead(flowId, nodeKey, refreshMergeTargets)])
        if (at !== epoch) return false
        // Keep each confirmed read even when the other request fails; actions stay locked.
        if (current.status === 'fulfilled') run.value = current.value
        if (saved.status === 'fulfilled') settings.value = saved.value
        if (saved.status === 'rejected') throw saved.reason
        if (current.status === 'rejected') throw current.reason
        error.value = null
        status.value = 'ready'
        return true
      } catch (cause) {
        if (at === epoch) { error.value = cause; status.value = 'error' }
        return false
      } finally { reading = null }
    })()
    return reading
  }
  async function send(): Promise<boolean> {
    if (!request) return false
    const saved = request
    const answer = await runGateDecide(saved.runId, nodeKey, saved.decision, saved.comment, saved.intent)
    epoch++
    run.value = answer
    request = null
    decisionError.value = null
    // A successful response is authoritative; settings are refreshed before another action.
    if (reading) await reading
    await reload()
    return true
  }
  async function replay(): Promise<boolean> {
    if (!request) return false
    if (replaying) return replaying
    replaying = (async () => {
      busy.value = request!.decision
      try {
        // GET precedes every retransmission, also after an SSE reconnect.
        if (!await reload()) return false
        return await send()
      } catch (cause) {
        decisionError.value = cause
        if (documentErrorOf(cause).kind !== 'network') request = null
        return false
      } finally { busy.value = null; replaying = null }
    })()
    return replaying
  }
  async function decide(decision: RunGateDecision, comment?: string): Promise<boolean> {
    if (busy.value || Object.keys(pending.value).length) return false
    if (request) return replay()
    if (status.value !== 'ready' || !gate.value?.can_decide || !node.value?.attemptId || settings.value?.settingsVersion === undefined) return false
    request = { runId: run.value!.runId, decision, comment, intent: { request_id: randomUuid(), expected_attempt_id: node.value.attemptId, expected_settings_version: settings.value.settingsVersion } }
    busy.value = decision
    decisionError.value = null
    try { return await send() }
    catch (cause) {
      decisionError.value = cause
      if (documentErrorOf(cause).kind === 'network') {
        busy.value = null
        return await replay()
      }
      request = null
      await reload()
      return false
    } finally { busy.value = null }
  }
  async function change(field: string, value: unknown): Promise<boolean> {
    if (!['archive', 'merge_mode', 'merge_target_branch', 'push', 'keep_branch'].includes(field)) return false
    if (settings.value?.settingsVersion === undefined || status.value !== 'ready' || settings.value?.locked || gate.value?.settings_locked || busy.value || request || Object.keys(pending.value).length) return false
    epoch++
    let refreshAfterFailure = false
    pending.value = { [field]: value }
    failure.value = null
    try {
      settings.value = await settingsNodeWrite(flowId, nodeKey, field, value, settings.value?.settingsVersion)
      decisionError.value = null
      return true
    } catch (cause) {
      failure.value = { field, error: cause }
      refreshAfterFailure = normalizeApiError(cause).params.reason !== 'legacy_merge_target_conflict'
      return false
    } finally {
      pending.value = {}
      // Invalidates reads started before the write's answer; failed writes keep the confirmed value.
      epoch++
      if (reading) await reading
      // A successful PATCH is authoritative. Re-read refused targets/versions; legacy conflicts preserve all saved fields.
      if (refreshAfterFailure) await reload()
    }
  }
  return { run, settings, node, gate, pending, status, error, failure, busy, decisionError, reload, decide, change, replay, hasRequest: () => request !== null }
}
const contexts = new Map<string, ReturnType<typeof createContext>>()
export function useGateContext(target: () => InspectorTarget | null) {
  const context = computed(() => {
    const key = gateOf(target())
    if (!key) return null
    if (!contexts.has(key)) contexts.set(key, createContext(...key.split('\n') as [string, string]))
    return contexts.get(key)!
  })
  watch(context, value => { if (value) void (value.hasRequest() ? value.replay() : value.reload()) }, { immediate: true })
  watch(() => `${target()?.nodeRun?.status ?? ''}\n${target()?.canvasState ?? ''}`, () => { void context.value?.reload() })
  useInspectorEvents(() => gateOf(target()) ? target() : null, {
    events: GATE_RUN_EVENTS, matches: gateRunEventMatches,
    changed: () => { const c = context.value; if (c) void (c.hasRequest() ? c.replay() : c.reload()) },
  })
  return context
}
