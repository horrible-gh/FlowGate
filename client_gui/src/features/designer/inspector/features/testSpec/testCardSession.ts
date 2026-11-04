// v8: one live controller per selected card; data and window drafts survive tab/card changes.
import { computed, effectScope, getCurrentInstance, onScopeDispose, reactive, ref, shallowRef, watch, type ComputedRef } from 'vue'
import {
  cardDocumentRead,
  cardDocumentStructuredRead,
  documentHistoryRead,
  testSpecMutationErrorOf,
  toTestSpecCaseDetail,
  type CardStructuredCommand,
  type TestSpecCaseDetail,
  type TestSpecView,
  type TestSpecCasePayload,
  type TestSpecResultEntry,
  type TestSpecFormatError,
} from '@/features/document'
import { cardDocumentContext, openDocumentAi } from '../../openDocument'
import { reviseTs, tsOwnerOf, tsStartNewIntent, type TsBase, type TsReviseResult, type TsSubmitted } from './tsEditSession'
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
/**
 * flowgate.v02.0127 T#5 — a TS v2 revision that was not saved (rejected / uncertain / busy / no card connection),
 * thrown to `changeDocument` so the refusal reads the same way as every other command's.
 */
export class TsReviseFailure extends Error {
  constructor(readonly result: Extract<TsReviseResult, { ok: false }>) {
    super(result.failure?.errorKey ?? result.kind)
  }
}

function tsFailureParsed(failure: TsReviseFailure) {
  const { result } = failure
  const code = result.failure?.code ?? null
  const status = result.failure?.status ?? result.error?.status ?? null
  const params = (result.failure?.params ?? {}) as Record<string, unknown>
  let kind: string
  if (result.kind === 'no_context') kind = 'binding'
  else if (result.kind === 'blocked') kind = code === 'idempotency_key_reused' ? 'reused' : 'config'
  else if (result.kind === 'busy' || result.kind === 'uncertain') kind = 'uncertain'
  else if (code === 'revision_conflict') kind = 'conflict'
  else if (code === 'slot_document_changed' || code === 'no_document') kind = 'detached'
  else if (code === 'unauthorized') kind = 'unauthorized'
  else if (code === 'token_kind_not_allowed') kind = 'config'
  else if (code === 'invalid_content' || code === 'invalid_request') kind = 'invalid'
  else if (code === 'GROUP_AI_RUN_LOCKED' || code === 'workflow_revert_pending') kind = 'running'
  else if (code === 'flow_disposed') kind = 'disposed'
  else if (code === 'permission_denied') kind = 'forbidden'
  else if (code === 'not_editable') {
    // §2.13: only an approved / terminal document is "approved"; an unreadable body or an unsupported type is not.
    const reason = typeof (result.failure?.params ?? {}).reason === 'string' ? String((result.failure?.params ?? {}).reason) : ''
    kind = reason === 'body_unreadable' || reason === 'unsupported_type' ? 'other' : 'approved'
  } else if (code === 'idempotency_key_reused') kind = 'reused'
  else if (code === 'node_not_found' || code === 'flow_not_found') kind = 'not_found'
  else kind = 'other'
  // invalid_content carries the structured validator's errors under errors[].validation.errors (§2.15.1).
  const errors = Array.isArray(params.errors) ? (params.errors as Record<string, unknown>[]) : []
  const inner = errors.flatMap((entry) => {
    const validation = entry.validation as Record<string, unknown> | undefined
    return Array.isArray(validation?.errors) ? (validation!.errors as Record<string, unknown>[]) : []
  })
  const fieldErrors: TestSpecFormatError[] = inner.map((row) => ({
    code: typeof row.code === 'string' ? row.code : null, caseId: typeof row.case_id === 'string' ? row.case_id : null,
    field: typeof row.field === 'string' ? row.field : null, message: typeof row.message === 'string' ? row.message : null,
  }))
  return { kind, status, code, message: null as string | null, fieldErrors }
}

type TsCaseList = readonly TestSpecCaseDetail[]
/** The cases draft's snapshot: base (doc, revision, coordinates) and the cases/title of the SAME v2 response. */
export interface CasesPin { base: TsBase; title: string | null; cases: TsCaseList }
/** The confirmed answer for a draft: the new base (N+1) and the pending's own submission (never the caller's current text). */
interface TsAck { base: TsBase; submitted: TsSubmitted; resent: boolean }

export const TEST_CARD_WINDOW_LOADERS = import.meta.glob('./windows/*Window.vue')
export const testCardWindowAvailable = (kind: TestCardWindowKind) => `./windows/${kind[0].toUpperCase() + kind.slice(1)}Window.vue` in TEST_CARD_WINDOW_LOADERS
const windowKinds: TestCardWindowKind[] = ['brief', 'scenario', 'case', 'report', 'follow']
const documentCommands: TestCardCommand[] = ['caseSave', 'caseAdd', 'caseDelete', 'rawSave', 'briefSave']
const missingCommands: TestCardCommand[] = ['rewriteCase', 'decide']

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
  // An emptied specification (its last case deleted) is shown as the empty scenario, not as a format error.
  const formatErrors = computed(() => (view.value?.details?.errors ?? []).filter((error) => error.code !== 'no_cases' || !!view.value?.details?.cases.length))
  // Only a successful raw save in an already open scenario may keep its repair surface.
  const rawSavedInScenario = ref(false)
  const rawRepairAllowed = computed(() => rawSavedInScenario.value && windows.scenario.open
    && formatErrors.value.length > 0
    && !formatErrors.value.some(error => ['missing_spec_section', 'unsupported_contract'].includes(error.code ?? '')))
  watch(() => windows.scenario.open, (open) => { if (!open) rawSavedInScenario.value = false }, { flush: 'sync' })
  watch(view, (value) => {
    if (value?.details?.cases.length || !value?.details?.errors.length) rawSavedInScenario.value = false
  }, { flush: 'sync' })
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
    if (!presentation.value.hasScenario) {
      // The empty scenario is an authoring surface too.
      if (windows.case.caseId !== '__new__') windows.case.open = false
    }
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
        read: settings.node.value !== null, blocked: settings.blocked.value,
        // flowgate.v02.0113 T#1: only an actually running card (server lock / node_running) or an unconfirmed run status
        locked: settings.node.value?.locked === true || current.nodeRun?.nodeRunning === true || current.editLock === 'unknown' })
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
    if (['caseSave', 'caseDelete'].includes(operation) && !presentation.value.hasScenario
      && !(operation === 'rawSave' && rawRepairAllowed.value)) return item('guard.no_case')
    if (documentCommands.includes(operation) && !view.value?.tsDocId) return item('reason.busy')
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
    if (kind === 'scenario') return view.value?.tsDocId ? null : 'reason.busy'
    return presentation.value.hasScenario || can('caseAdd').enabled ? null : 'guard.no_case'
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
    const parsed = error instanceof TsReviseFailure ? tsFailureParsed(error) : testSpecMutationErrorOf(error, operation === 'rawSave')
    const { kind, status, code, message, fieldErrors } = parsed
    if (documentCommands.includes(operation) && kind === 'forbidden') documentDenied.value = true
    const shared = ['running', 'disposed', 'forbidden', 'not_found', 'network', 'not_approved', 'report_approved', 'origin_pending', 'no_automated', 'unauthorized', 'config', 'detached', 'uncertain', 'binding']
    const failureOperation = operation === 'caseAdd' ? 'caseSave' : operation === 'briefSave' ? 'rawSave' : operation
    const key = kind === 'approved' ? 'guard.approved' : kind === 'forbidden' && documentCommands.includes(operation) ? `failure.${failureOperation}_forbidden` : shared.includes(kind) ? `failure.${kind}` : `failure.${failureOperation}_${['conflict', 'invalid'].includes(kind) ? kind : 'other'}`
    refusal.value = { operation, key, kind, status, code, message, fieldErrors, error }
  }
  /** T2 also uses this for its direct documentContentSave call, so raw saves share the guard/busy/refusal. */
  async function changeDocument(operation: 'caseSave' | 'caseDelete' | 'caseAdd' | 'rawSave' | 'briefSave', call: () => Promise<unknown>): Promise<boolean> {
    if (!can(operation).enabled) return false
    busy.value = operation; refusal.value = null
    let ok = false
    try {
      await call(); ok = true
      if (operation === 'rawSave' && windows.scenario.open) rawSavedInScenario.value = true
    } catch (error) { reject(operation, error) }
    finally { if (target()) await reload(); busy.value = null }
    return ok
  }
  // flowgate.v02.0127 T#5 (§2.15.3): the card's v2 coordinates of the TS and the snapshots the drafts pinned.
  // Review fix: every draft pins ONE v2 response — docId, revision, coordinates and the content/projection it shows
  // are taken from that same response and stay with that draft. A reopen, another draft's ACK or a late GET never
  // replaces them; only this draft's own confirmed ACK moves its base (to N+1, with the submitted cases).
  const tsContext = () => { const id = view.value?.tsDocId; return id ? cardDocumentContext(target(), id) : null }
  const caseDetails = (rows: unknown): TsCaseList => (Array.isArray(rows) ? rows : []).flatMap((row) => toTestSpecCaseDetail(row) ?? [])
  /** Reads one structured snapshot; null when it is not readable or not this card's TS. */
  async function pinCases(): Promise<CasesPin | null> {
    const ctx = tsContext()
    const expected = view.value?.tsDocId
    if (!ctx || !expected) return null
    try {
      const snapshot = await cardDocumentStructuredRead(ctx, 'ts_spec')
      const projection = snapshot.projection as { cases?: unknown; title?: unknown } | null | undefined
      if (snapshot.bodyState !== 'readable' || snapshot.docId !== expected || !projection || !Array.isArray(projection.cases)) return null
      return {
        base: { docId: snapshot.docId, revisionNo: snapshot.revisionNo, ctx },
        title: typeof projection.title === 'string' && projection.title ? projection.title : null,
        cases: caseDetails(projection.cases),
      }
    } catch { return null }
  }
  /** The brief's text and base from one structured read (null when it is not readable or not this TS). */
  async function pinBrief(): Promise<{ text: string; base: TsBase } | null> {
    const ctx = tsContext()
    if (!ctx) return null
    const snapshot = await cardDocumentStructuredRead(ctx, 'ts_brief')
    const projection = snapshot.projection as { text?: unknown } | null | undefined
    if (snapshot.bodyState !== 'readable' || snapshot.docId !== view.value?.tsDocId || typeof projection?.text !== 'string') return null
    return { text: projection.text, base: { docId: snapshot.docId, revisionNo: snapshot.revisionNo, ctx } }
  }
  /** The whole stored text, its frontmatter and base of one plain v2 read, for the raw editor. */
  async function pinRaw(): Promise<{ content: string; base: TsBase } | null> {
    const ctx = tsContext()
    if (!ctx) return null
    const snapshot = await cardDocumentRead(ctx)
    if (snapshot.bodyState !== 'readable' || snapshot.content === null || snapshot.docId !== view.value?.tsDocId) return null
    return { content: snapshot.content, base: { docId: snapshot.docId, revisionNo: snapshot.revisionNo, ctx } }
  }
  async function send(kind: 'raw' | 'brief' | 'cases', base: TsBase, payload: Parameters<typeof reviseTs>[2]): Promise<TsAck> {
    const result = await reviseTs(tsOwnerOf(kind, base.ctx, base.docId), base, payload)
    if (!result.ok) throw new TsReviseFailure(result)
    return { base: { docId: result.confirmed.docId, revisionNo: result.confirmed.revisionNo, ctx: base.ctx }, submitted: result.submitted, resent: result.resent }
  }
  const sameCommand = (a: CardStructuredCommand | null, b: CardStructuredCommand) => !!a && JSON.stringify(a) === JSON.stringify(b)
  /**
   * Cases save on the draft's own pin. `build` derives the cases array from the pin's cases (the same snapshot as
   * the base revision), so an array of revision N is never sent as N+1. When the document's kept request was resent
   * instead (an earlier uncertain save), only that submission becomes the pin's N+1 and this save is reported unsaved.
   */
  async function saveCases(pin: CasesPin, build: (cases: TsCaseList) => readonly TestSpecCasePayload[], operation: 'caseSave' | 'caseDelete' | 'caseAdd' = 'caseSave'): Promise<boolean> {
    const current = view.value
    if (!current?.tsDocId) return false
    let saved = false
    const ok = await changeDocument(operation, async () => {
      if (pin.base.docId !== current.tsDocId) throw new TsReviseFailure({ ok: false, kind: 'no_context', failure: null, error: null })
      const title = pin.title ?? current.details?.title ?? null
      const command: CardStructuredCommand = { kind: 'ts_spec', cases: build(pin.cases) as unknown as Record<string, unknown>[], ...(title ? { title } : {}) }
      const ack = await send('cases', pin.base, { command })
      // Move this pin to the confirmed N+1 with the submitted cases only.
      pin.base = ack.base
      if (ack.submitted.command?.kind === 'ts_spec') {
        pin.cases = caseDetails(ack.submitted.command.cases)
        if (ack.submitted.command.title) pin.title = ack.submitted.command.title
      }
      saved = sameCommand(ack.submitted.command, command)
      if (!saved) throw new TsReviseFailure({ ok: false, kind: 'busy', failure: null, error: null })
    })
    return ok && saved
  }
  /** Brief save: the draft's own pinned base; returns the confirmed N+1 base and the text the server stored for that request. */
  async function reviseBrief(base: TsBase, text: string): Promise<{ base: TsBase; text: string; same: boolean }> {
    const ack = await send('brief', base, { command: { kind: 'ts_brief', text } })
    const stored = ack.submitted.command?.kind === 'ts_brief' ? ack.submitted.command.text : text
    return { base: ack.base, text: stored, same: stored === text }
  }
  /** Raw save: the whole stored text on the raw draft's pinned base; returns N+1 and the submitted text. */
  async function reviseRaw(base: TsBase, text: string): Promise<{ base: TsBase; text: string; same: boolean }> {
    const ack = await send('raw', base, { raw: text })
    const stored = ack.submitted.text ?? text
    return { base: ack.base, text: stored, same: stored === text }
  }
  /** An explicit new intent for a draft whose kept request was blocked (key reuse / corrected setting). */
  function startNewIntent(kind: 'raw' | 'brief' | 'cases', base: TsBase) { tsStartNewIntent(base.docId, tsOwnerOf(kind, base.ctx, base.docId)) }

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
    can, canOpen, openReason, openWindow, closeWindow, reload, reject, changeDocument, saveCases, pinCases, pinBrief, pinRaw, reviseBrief, reviseRaw, startNewIntent, changeSetting, rewriteAll,
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
