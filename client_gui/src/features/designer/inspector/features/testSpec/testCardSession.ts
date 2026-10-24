// v8: one live controller per selected card; data and window drafts survive tab/card changes.
import { computed, effectScope, getCurrentInstance, onScopeDispose, reactive, ref, shallowRef, watch, type ComputedRef } from 'vue'
import { documentHistoryRead, testSpecSave, testSpecMutationErrorOf, type TestSpecView, type TestSpecCasePayload, type TestSpecResultEntry, type TestSpecFormatError } from '@/features/document'
import { openDocumentAi } from '../../openDocument'
import { captureInspectorResourceContext, useInspectorResource } from '../../useInspectorResource'
import type { InspectorTarget } from '../../inspectorTarget'
import { cardSettingsDisabledReason, type CardSettingsField } from '../settings/cardSettingsModel'
import { useCardSettings } from '../settings/useCardSettings'
import { testCardPresentation, testSpecTargetDocId, type TestCardWriting, type TestSpecAvailabilityItem } from './testSpecModel'
import { useTestSpec } from './useTestSpec'

export type TestCardWindowKind = 'brief' | 'scenario' | 'case' | 'report' | 'follow'
export type TestCardCommand = 'run' | 'cancel' | 'rerunCase' | 'resultEntry' | 'caseSave' | 'caseAdd' | 'caseDelete' | 'rawSave' | 'briefSave' | 'rewriteAll' | 'rewriteCase' | 'settingChange' | 'decide' | 'follow'
export interface TestCardWindowState {
  open: boolean
  minimized: boolean
  zIndex: number
  caseId: string | null
  /** Consumers retain shell geometry and unsaved drafts here, keyed by window/case. */
  geometry: Record<string, number>
}
export interface TestCardRefusal {
  operation: TestCardCommand; key: string; kind: string; status: number | null; code: string | null
  message: string | null; fieldErrors: readonly TestSpecFormatError[]; error: unknown
}
export const TEST_CARD_WINDOW_LOADERS = import.meta.glob('./windows/*Window.vue')
export const testCardWindowAvailable = (kind: TestCardWindowKind) => `./windows/${kind[0].toUpperCase() + kind.slice(1)}Window.vue` in TEST_CARD_WINDOW_LOADERS
const windowKinds: TestCardWindowKind[] = ['brief', 'scenario', 'case', 'report', 'follow']
const documentCommands: TestCardCommand[] = ['caseSave', 'caseAdd', 'caseDelete', 'rawSave', 'briefSave']
const missingCommands: TestCardCommand[] = ['caseAdd', 'briefSave', 'rewriteCase', 'decide']

export function createTestCardSession(target: () => InspectorTarget | null) {
  const writing = ref<TestCardWriting>(null)
  const writingCaseId = ref<string | null>(null)
  const test = useTestSpec(target, () => writing.value !== null)
  const settings = useCardSettings(target)
  const reviewHistory = useInspectorResource({
    target: () => { const current = target(); const docId = test.view.value?.tsDocId ?? testSpecTargetDocId(current)
      return current && docId ? { projectId: current.projectId, flowId: current.flowId, docId } : null },
    read: documentHistoryRead, events: ['doc_review_status_changed', 'document_explorer_refresh'],
  })
  const lastNormal = shallowRef<TestSpecView | null>(null)
  const lastTarget = shallowRef<InspectorTarget | null>(target())
  const busy = ref<TestCardCommand | null>(null)
  const refusal = shallowRef<TestCardRefusal | null>(null)
  const documentDenied = ref(false)
  const settingsDenied = ref(false)
  const drafts = reactive<Record<string, unknown>>({})
  const windows = reactive(Object.fromEntries(windowKinds.map((kind) => [kind, {
    open: false, minimized: false, zIndex: 0, caseId: null, geometry: {},
  }])) as Record<TestCardWindowKind, TestCardWindowState>)
  let front = 0
  let settlingWriting = false
  let submittedWriting = false
  const reportRecentlyConfirmed = ref(false)
  let confirmationTimer: ReturnType<typeof setTimeout> | undefined
  watch(target, (value) => { if (value) lastTarget.value = value }, { flush: 'sync' })
  watch(test.view, (value) => { if (value && !writing.value && value.details?.complete !== false) lastNormal.value = value }, { immediate: true, flush: 'sync' })
  const view = computed(() => lastNormal.value)
  const cases = computed(() => view.value?.details?.cases ?? [])
  const rows = computed(() => view.value?.details?.rows ?? [])
  const formatErrors = computed(() => view.value?.details?.errors ?? [])
  const presentation = computed(() => testCardPresentation(view.value, writing.value, rows.value))
  const instructionOmitted = computed(() => settings.node.value?.fields.instruction?.value === 'omit')
  watch(() => presentation.value.stage, (stage, previous) => {
    clearTimeout(confirmationTimer)
    reportRecentlyConfirmed.value = previous === 5 && stage === 6
    if (reportRecentlyConfirmed.value) confirmationTimer = setTimeout(() => { reportRecentlyConfirmed.value = false }, 1800)
  })
  onScopeDispose(() => clearTimeout(confirmationTimer))

  async function reload() { await Promise.all([test.reload(), settings.reload(), reviewHistory.reload()]) }

  // An AI run on another document only supplies the Flow guard. It never becomes scenario writing.
  watch([() => test.guard.value, () => view.value?.tsDocId ?? null, () => target() !== null], async ([guard, tsId, active]) => {
    if (!active || !guard) return
    if (tsId && guard.writingDocument === tsId) {
      if (!writing.value) writing.value = presentation.value.hasScenario ? 'rewrite-all' : 'initial'
    } else if (writing.value && !submittedWriting && !settlingWriting) {
      settlingWriting = true
      try { await reload(); if (test.status.value === 'ready' && test.view.value && test.view.value.details?.complete !== false) lastNormal.value = test.view.value }
      finally { writing.value = null; writingCaseId.value = null; settlingWriting = false }
    }
  }, { flush: 'sync' })

  // The document may have been regenerated, but not temporarily absent during writing/reloads.
  watch([() => test.status.value, () => writing.value, instructionOmitted], () => {
    if (instructionOmitted.value) windows.brief.open = false
    if (test.status.value !== 'ready' || writing.value) return
    if (!presentation.value.hasScenario) { windows.scenario.open = false; windows.case.open = false }
    if (!presentation.value.hasReport) windows.report.open = false
    if (!presentation.value.canFollow) windows.follow.open = false
  })
  watch(() => presentation.value.canFollow, (allowed) => { if (!allowed && !writing.value) windows.follow.open = false })
  watch(() => target() === null, (inactive) => {
    if (inactive) { refusal.value = null; documentDenied.value = false; settingsDenied.value = false }
  })
  watch(() => settings.failure.value?.kind, (kind) => { if (kind === 'forbidden') settingsDenied.value = true })

  function can(operation: TestCardCommand, caseId?: string, field?: CardSettingsField): TestSpecAvailabilityItem {
    const current = target()
    const item = (reason: string | null, visible = true) => ({ visible, enabled: !reason, reason })
    if (operation === 'cancel') return test.availability.value.cancel
    if (current?.readOnly) return item(`reason.read_only_${current.readOnly}`)
    if (!current) return item('reason.busy')
    if (test.guard.value?.groupDisposed || settings.blocked.value === 'disposed') return item('reason.disposed')
    if (writing.value) return item('guard.writing')
    if (operation === 'settingChange') {
      const reason = cardSettingsDisabledReason({ readOnly: current.readOnly, placing: settings.placing.value,
        read: settings.node.value !== null, locked: settings.node.value?.locked === true || !!current.editLock, blocked: settings.blocked.value })
      if (reason) return item(reason.startsWith('reason.read_only') || reason === 'reason.disposed' ? reason : 'guard.' + reason.slice(7))
      if (settingsDenied.value) return item('reason.forbidden')
      return item(field && field in settings.pending.value ? 'reason.busy' : null)
    }
    if (operation === 'follow') return item(presentation.value.canFollow ? null : 'guard.no_result')
    if (test.guard.value?.aiRunning) return item('reason.ai_running')
    if (documentCommands.includes(operation) && documentDenied.value) return item('reason.forbidden')
    if (documentCommands.includes(operation) || operation === 'rewriteAll' || operation === 'rewriteCase') {
      if (!test.guard.value) return item('reason.busy')
      if (test.guard.value.editable === false) return item('guard.approved')
    }
    if (['run', 'rerunCase', 'resultEntry'].includes(operation)) {
      if (!test.view.value) return item('reason.busy')
      const reason = operation === 'run' ? test.runDisabledReason.value : test.caseDisabledReason.value
      if (reason) return item(reason)
      if (operation === 'run') return item(null, test.form.value !== 'legacy')
      const found = view.value?.cases.find((c) => c.caseId === caseId)
      if (!found) return item('guard.no_case')
      if (operation === 'rerunCase' && (found.executionMode !== 'automated' || view.value?.caseCapabilities[caseId!] !== 'case_selectable')) return item('guard.case_unavailable')
      if (operation === 'resultEntry' && found.executionMode === 'automated' && ['case_selectable', 'suite_only'].includes(view.value?.caseCapabilities[caseId!] ?? '')) return item('guard.server_result')
    }
    if (operation === 'rewriteAll' && test.runState.value.active) return item('reason.run_active')
    if (['caseSave', 'caseDelete', 'rawSave', 'rewriteAll'].includes(operation) && !presentation.value.hasScenario) return item('guard.no_case')
    if (missingCommands.includes(operation)) return item('guard.unconnected')
    if (busy.value || test.busy.value) return item('reason.busy')
    return item(null)
  }

  /** Shared by menu and section cards; the availability argument also supports window-less tests. */
  function openReason(kind: TestCardWindowKind, available = testCardWindowAvailable(kind)): string | null {
    if (!available) return 'guard.unconnected'
    if (kind === 'brief') return instructionOmitted.value ? 'guard.instruction_omitted' : null
    if (writing.value === 'initial' || (kind === 'case' && writing.value)) return 'guard.writing'
    if (kind === 'follow') return presentation.value.canFollow ? null : 'guard.no_result'
    if (kind === 'report') return presentation.value.hasReport ? null : 'guard.no_result'
    return presentation.value.hasScenario ? null : 'guard.no_case'
  }
  function canOpen(kind: TestCardWindowKind): boolean { return openReason(kind, true) === null }
  function openWindow(kind: TestCardWindowKind, caseId: string | null = null): boolean {
    if (!canOpen(kind)) return false
    const window = windows[kind]
    window.open = true; window.minimized = false; window.caseId = caseId; window.zIndex = ++front
    return true
  }
  function closeWindow(kind: TestCardWindowKind) { windows[kind].open = false }

  function reject(operation: TestCardCommand, error: unknown) {
    if (!target()) return
    const parsed = testSpecMutationErrorOf(error, operation === 'rawSave')
    const { kind, status, code, message, fieldErrors } = parsed
    if (documentCommands.includes(operation) && kind === 'forbidden') documentDenied.value = true
    const shared = ['running', 'disposed', 'forbidden', 'not_found', 'network', 'not_approved', 'report_approved', 'origin_pending', 'no_automated']
    const key = kind === 'approved' ? 'guard.approved' : kind === 'forbidden' && documentCommands.includes(operation) ? `failure.${operation}_forbidden` : shared.includes(kind) ? `failure.${kind}` : `failure.${operation}_${['conflict', 'invalid'].includes(kind) ? kind : 'other'}`
    refusal.value = { operation, key, kind, status, code, message, fieldErrors, error }
  }
  /** T2 also uses this for its direct documentContentSave call, so raw saves share the guard/busy/refusal. */
  async function changeDocument(operation: 'caseSave' | 'caseDelete' | 'rawSave', call: () => Promise<unknown>): Promise<boolean> {
    if (!can(operation).enabled) return false
    busy.value = operation; refusal.value = null
    let ok = false
    try { await call(); ok = true } catch (error) { reject(operation, error) }
    finally { if (target()) await reload(); busy.value = null }
    return ok
  }
  async function saveCases(payload: readonly TestSpecCasePayload[], operation: 'caseSave' | 'caseDelete' = 'caseSave'): Promise<boolean> {
    const current = view.value
    if (!current?.tsDocId) return false
    return changeDocument(operation, () => testSpecSave(current.tsDocId!, payload, current.details?.title ?? null))
  }
  async function changeSetting(field: CardSettingsField, value: unknown): Promise<boolean> {
    if (!can('settingChange', undefined, field).enabled) return false
    return settings.change(field, value)
  }
  async function rewriteAll(): Promise<boolean> {
    if (!can('rewriteAll').enabled || !view.value?.tsDocId) return false
    // Opening the provider dialog does not write. Its submit hook guards and locks synchronously,
    // including the request and the interval before the server's first activity event.
    const current = target()!
    const settled = () => {
      void (async () => {
        try { await reload(); if (test.view.value?.details?.complete !== false && test.view.value) lastNormal.value = test.view.value }
        finally { submittedWriting = false; writing.value = null; writingCaseId.value = null }
      })()
    }
    openDocumentAi(view.value.tsDocId, current, settled, {
      submitting: () => {
        if (!can('rewriteAll').enabled) return false
        submittedWriting = true
        writing.value = presentation.value.hasScenario ? 'rewrite-all' : 'initial'
        return true
      },
      rejected: settled,
    })
    return true
  }
  return { target: computed(() => target() ?? lastTarget.value), test, settings, reviewHistory, view, cases, rows, formatErrors,
    lastNormal, presentation, writing, writingCaseId, instructionOmitted, busy, refusal, drafts, windows, reportRecentlyConfirmed,
    can, canOpen, openReason, openWindow, closeWindow, reload, reject, changeDocument, saveCases, changeSetting, rewriteAll,
    run: () => can('run').enabled ? test.run() : Promise.resolve(false),
    cancel: () => can('cancel').enabled ? test.cancel() : Promise.resolve(false),
    rerunCase: (caseId: string) => can('rerunCase', caseId).enabled ? test.rerunCase(caseId) : Promise.resolve(false),
    enterResult: (result: TestSpecResultEntry) => can('resultEntry', result.case_id).enabled ? test.enterResult(result) : Promise.resolve(false),
  }
}
export type TestCardContext = ReturnType<typeof createTestCardSession>

interface SessionEntry { context: TestCardContext; target: ReturnType<typeof shallowRef<InspectorTarget | null>>; users: number }
const apps = new WeakMap<object, Map<string, SessionEntry>>()
const fallbackApp = {}
export const testCardSessionKey = (target: InspectorTarget) => [target.projectId, target.flowId, target.nodeType ? target.nodeId : target.docId ?? target.nodeId].join('\n')

/** Consumers share refs, settings writes and event subscriptions. Inactive cards retain only their memory. */
export function useTestCardSession(target: () => InspectorTarget | null): ComputedRef<TestCardContext | null> {
  const app = getCurrentInstance()?.appContext ?? fallbackApp
  const inResourceContext = captureInspectorResourceContext()
  let sessions = apps.get(app)
  if (!sessions) { sessions = new Map(); apps.set(app, sessions) }
  const current = shallowRef<SessionEntry | null>(null)
  const stop = watch(() => target() ? testCardSessionKey(target()!) : null, (key, _before, cleanup) => {
    if (!key) { current.value = null; return }
    let entry = sessions!.get(key)
    if (!entry) {
      const selected = shallowRef(target())
      const scope = effectScope(true)
      const context = inResourceContext(() => scope.run(() => createTestCardSession(() => selected.value)))!
      entry = { context, target: selected, users: 0 }; sessions!.set(key, entry)
      // App disposal naturally releases the WeakMap; inactive resources unsubscribe through target=null.
    }
    entry.users++; entry.target.value = target(); current.value = entry
    cleanup(() => { if (--entry!.users === 0) entry!.target.value = null })
  }, { immediate: true, flush: 'sync' })
  const stopTarget = watch(target, (value) => { if (value && current.value) current.value.target.value = value }, { flush: 'sync' })
  onScopeDispose(() => { stopTarget(); stop() })
  return computed(() => current.value?.context ?? null)
}
