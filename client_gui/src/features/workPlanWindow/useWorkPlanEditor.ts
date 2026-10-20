// flowgate.v02.0072 T#1 — the state and the calls of one work plan edit window: the read (`GET …/work-plan`), the
// edits (workPlanEditModel), the one save (`PUT …/work-plan` with base_revision_no — the whole body, every unsaved
// change of the window at once) and what a save may answer: someone saved first (409, reload), field errors (422,
// pinned to their rows), the capability confirmation (422, sent again with the findings acknowledged), not editable
// any more (422), the Flow's AI run (423).
// - Locked (nothing can be changed, [저장] off): the opener's read-only state or Flow run lock, the Flow's AI run
//   (the run read documentAiWindows follows), an upload of this document in progress (documentEditRegistry), the
//   server's editable / edit_locked_reason, and a save the server refused for good (422 detail not editable, 409
//   detail the Flow discarded — kept, since the read's editable does not check the discard). Nothing is sent while
//   locked; the server checks again anyway. A refused save keeps the edits and reads the lock state again.
// - Reading again over a shown plan ([다시 읽기] after the person dropped their edits) locks every input until the
//   answer lands: what is typed meanwhile would be replaced by it.
// - External changes: the window's project events and its polling (useDocumentWindowEvents, called by the window)
//   ask for `refresh()`. A clean window takes the new plan; a window with unsaved edits keeps them and says a newer
//   revision exists (its save would answer 409) — nothing a person typed is replaced without asking. Every refresh
//   (and `refreshActivity()`, the window's Flow / record events) reads the Flow's live AI run again, so a run started
//   in another tab locks the window without a save first, and its end unlocks it.
// - The window registers with the document edit registry (as the document windows do), so an upload of this
//   document waits while it holds unsaved edits, and a finished upload is read again here.
// - The Step window (T#2) edits one row through `stepSession(rowId)`: a copy of the row's pre-instruction values,
//   and `apply` that puts its values on that row only (parent dirty; the server PUT stays the parent's).
// - flowgate.v02.0073 T#2: a row's review is set by the main window's review popover (`setStepReview`, the server's
//   review_count_choices, the reviewer picker `reviewerOptions`) straight on the draft; the Step window no longer
//   carries it, so a Step window's save or cancel never writes over a review set meanwhile. Its target names the row
//   by its card and document (cardType / cardOrdinal) and its place among the shown rows — the Step key stays inside.
// - Card moves take the screen's positions among the shown cards (flowgate.v02.0073 T#1: DS / T2 cards are not drawn);
//   the model puts the shown cards back into their slots of the plan, so a hidden card keeps its place.
// - flowgate.v02.0073 T#4: JSON on the client only (workPlanJson). `exportJson` is the draft as the save would send it;
//   `importJson` puts a checked JSON in the draft's place as an edit (dirty; nothing is sent — the save is the one PUT,
//   whose server check is the final one). Not while locked or while a Step window holds edits; the Step windows open
//   on the plan it replaces lose their row (the plan epoch moves, as on a read of another plan).
// - flowgate.v02.0074 T#5 (D#1 §3.7 · §6.2): 「지시 생략」 of a row (`omitStep`, its rules workPlanOmitState). The deploy
//   status (`GET …/work-plan/deploy`) is read when the plan is read and after every save; a plan that cannot deploy (or
//   whose status read failed) offers no 「지시 생략」. One click, one at a time, in order: the fixed text on the row's
//   pre-instruction through the Step window's own path (applyStepDraft, origin human), the one PUT of this window (a
//   refusal puts the row and the window back as they were, the save's failure says why — a capability confirmation is
//   not given on the person's behalf: it is a failed save too), then the deploy of the saved revision: a status read for
//   the mode (first with no deployed card, else add), a preview, and the execution with that preview's baseline (nothing
//   runs on a blocker or no change). Only the answer's own entry for the row's card says how it went (omitCardVerdict):
//   written is the success; kept / another type / placed twice / an unregistered type / not shown are told as such, from
//   the execution or from a preview with no change. A deploy that fails after the save (or whose answer does not show the
//   row's card) leaves that row's button as 「전개 재시도」 — each row its own, until a deploy answer settles its card or the
//   row is gone: the preview and the execution again (a new preview each time), never the save.
import { computed, onScopeDispose, ref, shallowRef, watch, type ComputedRef, type Ref, type ShallowRef } from 'vue'
import {
  workPlanDeployExecute,
  workPlanDeployFailureOf,
  workPlanDeployPreview,
  workPlanDeployStatus,
  workPlanEditRead,
  workPlanErrorOf,
  workPlanSave,
  workPlanSaveFailureOf,
  type WorkPlanCapabilityFinding,
  type WorkPlanDeployFailureKind,
  type WorkPlanDeployStatus,
  type WorkPlanErrorKind,
  type WorkPlanSaveFailure,
} from '@/features/document'
import type { WpFieldError } from '@shared/workPlanErrors'
import type { SelectOption } from '@/components/ui/Select.vue'
import { documentAiActivityOf, readDocumentAiActivity } from '@/features/documentWindow/documentAiWindows'
import { documentIoGuarded, registerDocumentEditEntry } from '@/features/documentWindow/documentEditRegistry'
import {
  WORK_PLAN_OMIT_TEXT,
  applyDefaults as applyDefaultsTo,
  applyQuantity,
  applyStepDraft,
  bodyOf,
  cardType,
  pairedDesignBody,
  instructionEligible,
  setInstructionMode as setInstructionModeIn,
  omitCardVerdict,
  canMoveShownCardBy,
  draftOf,
  editBodyOf,
  errorsByRow,
  moveShownCard,
  planQuantity,
  providerName,
  providerOptions,
  rowOf,
  savedDraft,
  setDefaultNote as setDefaultNoteIn,
  setDefaultProvider as setDefaultProviderIn,
  setStepNote as setStepNoteIn,
  setStepProvider as setStepProviderIn,
  setStepReview as setStepReviewIn,
  shownCardsOf,
  stepDraftOf,
  toWorkPlanEditView,
  unassignedSteps,
  unitOf,
  workPlanLockReason,
  workPlanOmitState,
  type WorkPlanDraft,
  type WorkPlanOmitCardVerdict,
  type WorkPlanOmitDeployCard,
  type WorkPlanEditView,
  type WorkPlanLockReason,
  type WorkPlanOmitState,
  type WorkPlanPairRole,
  type WorkPlanRefusal,
  type WorkPlanStepDraft,
} from './workPlanEditModel'
import { readWorkPlanJson, workPlanJsonText, type WorkPlanJsonIssue } from './workPlanJson'

export type WorkPlanEditorPhase = 'loading' | 'ready' | 'unreadable' | 'error'

/** A short line the window shows after an action (what happened, or why nothing did). */
export interface WorkPlanEditorNotice {
  readonly key:
    | 'saved'
    | 'saved_unassigned'
    | 'applied_defaults'
    | 'quantity_blocked_started'
    | 'quantity_unsupported'
    | 'move_started'
    | 'move_before_started'
    | 'imported'
    | 'imported_unchanged'
    | 'omitted'
    | 'omit_kept'
    | 'omit_mismatch'
    | 'omit_duplicate'
    | 'omit_unregistered'
    | 'omit_unconfirmed'
    | 'omit_no_change'
    | 'omit_deploy_failed'
  readonly params?: Readonly<Record<string, string | number>>
  readonly tone: 'success' | 'warning'
  /** A second message key (this window's) said after the first, e.g. why a deploy failed. */
  readonly detail?: string
}

/**
 * How a 「지시 생략」 ended: deployed (its card's instruction line written), the line kept as it was (instruction_kept),
 * the card of another type (kept_type_mismatch), placed more than once (kept_duplicate) or of an unregistered type
 * (skipped_type), the answer not showing the card (unconfirmed, 「전개 재시도」 offered), nothing to deploy, the save
 * refused (the row back as it was), saved but not deployed (「전개 재시도」 offered), or not started (off now).
 */
export type WorkPlanOmitOutcome =
  | 'omitted'
  | 'kept'
  | 'mismatch'
  | 'duplicate'
  | 'unregistered'
  | 'unconfirmed'
  | 'no_change'
  | 'save_failed'
  | 'deploy_failed'
  | 'blocked'

/** A card the deploy answer settled without writing the row's instruction: its warning and outcome. */
const OMIT_SETTLED: Readonly<Record<'kept' | 'mismatch' | 'duplicate' | 'unregistered', { key: WorkPlanEditorNotice['key']; outcome: WorkPlanOmitOutcome }>> = {
  kept: { key: 'omit_kept', outcome: 'kept' },
  mismatch: { key: 'omit_mismatch', outcome: 'mismatch' },
  duplicate: { key: 'omit_duplicate', outcome: 'duplicate' },
  unregistered: { key: 'omit_unregistered', outcome: 'unregistered' },
}

/** What a JSON import did (0073 T#4): taken (dirty), the same plan, refused for the lock or a Step window, or invalid. */
export type WorkPlanJsonImportResult =
  | { readonly status: 'imported' | 'unchanged' | 'locked' | 'step_pending' }
  | { readonly status: 'invalid'; readonly issues: readonly WorkPlanJsonIssue[] }

/** A quantity decrease waiting for its confirmation (it removes Steps that carry values). */
export interface WorkPlanPendingQuantity {
  readonly type: string
  readonly count: number
  readonly valueSteps: number
}

/** The row a Step window edits, as it is now. */
export interface WorkPlanStepEditTarget {
  readonly docId: string
  /** The row's identity (card id | role); follows a new card's id after a save. */
  readonly rowId: string
  /** The Step key now (renumbered by card moves; for the upload API and tests — never shown to a person). */
  readonly stepKey: string
  /** 1-based position among the shown rows (the main window's row number; the whole plan's for a hidden row). */
  readonly stepNo: number
  readonly type: string
  readonly pairRole: WorkPlanPairRole
  /** The card the row is on, as the main window names it: its head type, unit and ordinal (작업 1, 기본 설계 1). */
  readonly cardType: string
  readonly cardUnit: 'sheet' | 'set'
  readonly cardOrdinal: number
}

export type WorkPlanStepApplyStatus = 'applied' | 'gone' | 'locked' | 'not_eligible'

/** What the parent window gives a Step window (T#2) for one row. Read at call time. */
export interface WorkPlanStepEditSession {
  /** The row now, or null when it is gone (its card removed, the plan read again without it, the window closed). */
  target(): WorkPlanStepEditTarget | null
  /** A copy of the row's pre-instruction values, or null (gone / locked Step). */
  snapshot(): WorkPlanStepDraft | null
  /** Puts the values on this row only; the parent becomes dirty (when they differ) and saves them with its [저장]. */
  apply(values: WorkPlanStepDraft): WorkPlanStepApplyStatus
  /** The parent cannot be changed now (any lock reason, a save running, a capability confirmation pending). */
  locked(): boolean
  lockReason(): WorkPlanLockReason | null
  /** The parent window is still open. */
  alive(): boolean
  preInstructionMaxChars(): number
  attachmentMaxBytes(): number
  /** The stored Step key accepted by the upload API; null until a new card is saved by its parent. */
  attachmentStepKey(): string | null
  /** Refresh after an upload refusal without discarding either editor's draft. */
  refreshLocks(refusal?: WorkPlanRefusal): void
}

export interface WorkPlanEditorOptions {
  docId(): string
  /** The document's Flow (v0.1 group id) whose AI run locks the plan, or null. */
  groupId(): string | null
  /** The opener's verdict: read only (ownership conflict / revision view) or the Flow's run lock; null = none. */
  external(): 'read_only' | 'run_locked' | null
  /** Child drafts / in-flight reserved uploads must survive refresh and must precede a parent PUT. */
  stepPending?(): boolean
  /** Told after every successful save. */
  saved?(revisionNo: number): void
  /** The row's Step window is open (its 「지시 생략」 waits for it). */
  stepOpen?(rowId: string): boolean
}

export interface WorkPlanEditor {
  readonly phase: Readonly<Ref<WorkPlanEditorPhase>>
  readonly loadError: Readonly<Ref<WorkPlanErrorKind | null>>
  /** The stored text of a plan the server cannot read as a table (409 wp_unreadable), when it sent it. */
  readonly unreadableRaw: Readonly<Ref<string | null>>
  readonly view: Readonly<ShallowRef<WorkPlanEditView | null>>
  readonly draft: Readonly<ShallowRef<WorkPlanDraft | null>>
  readonly dirty: Readonly<Ref<boolean>>
  readonly saving: Readonly<Ref<boolean>>
  /** A read again over the shown plan is on its way (every input waits for it). */
  readonly reloading: Readonly<Ref<boolean>>
  readonly lockReason: ComputedRef<WorkPlanLockReason | null>
  /** No control may change the plan now (lock, save running, read again running, capability confirmation pending, not ready). */
  readonly inputsLocked: ComputedRef<boolean>
  readonly canSave: ComputedRef<boolean>
  /** The last save's failure (not the capability confirmation), until the next save or read. */
  readonly failure: Readonly<ShallowRef<WorkPlanSaveFailure | null>>
  readonly rowErrors: Readonly<ShallowRef<Readonly<Record<string, readonly WpFieldError[]>>>>
  readonly topErrors: Readonly<ShallowRef<readonly WpFieldError[]>>
  readonly capability: Readonly<ShallowRef<readonly WorkPlanCapabilityFinding[] | null>>
  /** A newer revision stored elsewhere while this window holds unsaved edits, or null. */
  readonly externalRevision: Readonly<Ref<number | null>>
  readonly pendingQuantity: Readonly<ShallowRef<WorkPlanPendingQuantity | null>>
  readonly notice: Readonly<ShallowRef<WorkPlanEditorNotice | null>>
  readonly unassigned: ComputedRef<number>
  /** First read, or a read again that drops unsaved edits (the caller asked first). */
  load(): Promise<boolean>
  /** An external change signal: a clean window reads the plan again; a dirty one only learns the new revision. */
  refresh(): Promise<void>
  /** Reads the Flow's live AI run again (the lock follows it). */
  refreshActivity(): void
  save(): Promise<'saved' | 'capability' | 'failed' | 'blocked'>
  confirmCapability(): Promise<'saved' | 'capability' | 'failed' | 'blocked'>
  cancelCapability(): void
  setQuantity(type: string, next: number): void
  confirmQuantity(): void
  cancelQuantity(): void
  /** `from` / `to` are positions among the shown cards (the screen's), not draft.cards indexes. */
  moveCard(from: number, to: number): void
  moveCardBy(position: number, delta: number): void
  canMoveCardBy(position: number, delta: number): boolean
  cardStarted(cardId: string): boolean
  setStepProvider(rowId: string, providerId: string | null): void
  setStepNote(rowId: string, note: string): void
  setInstructionMode(rowId: string, omitted: boolean): void
  /** The review popover: a count the server offers (or the row's own), and a reviewer (null = project default). */
  setStepReview(rowId: string, reviewCount: number, reviewerProviderId: string | null): void
  /** The review counts the server takes (GET review_count_choices). */
  reviewCountChoices(): readonly number[]
  /** The reviewer picker's options (empty = the project's default reviewer at run time). */
  reviewerOptions(emptyLabel: string, unavailable: (name: string) => string): SelectOption[]
  setDefaultProvider(providerId: string | null): void
  setDefaultNote(note: string): void
  applyDefaults(): void
  providerOptions(used: readonly (string | null)[], emptyLabel: string, unavailable: (name: string) => string): SelectOption[]
  providerName(providerId: string, snapshot?: string | null): string
  stepTarget(rowId: string): WorkPlanStepEditTarget | null
  stepSession(rowId: string): WorkPlanStepEditSession
  /** The draft as JSON (the body the save would send, unsaved edits included), or null before a plan is read. */
  exportJson(): string | null
  /** Puts a JSON plan in the draft's place (checked first); the window becomes dirty, [저장] sends it. */
  importJson(text: string): WorkPlanJsonImportResult
  /** The plan's deploy status read (when the plan is read and after a save), or null (not read, or it failed). */
  readonly deployStatus: Readonly<ShallowRef<WorkPlanDeployStatus | null>>
  /** The row whose 「지시 생략」 is on its way, or null. */
  readonly omitting: Readonly<Ref<string | null>>
  /** Whether the row offers 「지시 생략」 (or 「전개 재시도」) now, and why not. */
  omitState(rowId: string): WorkPlanOmitState
  /** 「지시 생략」 of the row (or its 「전개 재시도」): write, save once, deploy. */
  omitStep(rowId: string): Promise<WorkPlanOmitOutcome>
  dismissNotice(): void
  /** The window is closing: sessions report not alive, late answers are dropped. */
  dispose(): void
}

/** The deploy failures this window names (deploy_failure.*); any other is `other`. */
const OMIT_DEPLOY_FAILURES: ReadonlySet<WorkPlanDeployFailureKind> = new Set<WorkPlanDeployFailureKind>([
  'forbidden',
  'ai_running',
  'owned_by_v01',
  'ownership_conflict',
  'run_locked',
  'not_on_card',
  'not_editable',
  'disposed',
  'wp_changed',
  'attachment_unreadable',
  'draft_conflict',
  'blocked',
  'preview_stale',
  'target_changed',
  'restore_incomplete',
  'network',
])

function omitDeployFailure(kind: WorkPlanDeployFailureKind): string {
  return OMIT_DEPLOY_FAILURES.has(kind) ? kind : 'other'
}

export function useWorkPlanEditor(options: WorkPlanEditorOptions): WorkPlanEditor {
  const phase = ref<WorkPlanEditorPhase>('loading')
  const loadError = ref<WorkPlanErrorKind | null>(null)
  const unreadableRaw = ref<string | null>(null)
  const view = shallowRef<WorkPlanEditView | null>(null)
  const draft = shallowRef<WorkPlanDraft | null>(null)
  const dirty = ref(false)
  const saving = ref(false)
  const reloading = ref(false)
  /** A save the server refused for good; 'disposed' stays for the window's life (the read cannot tell). */
  const refused = ref<WorkPlanRefusal | null>(null)
  const failure = shallowRef<WorkPlanSaveFailure | null>(null)
  const rowErrors = shallowRef<Readonly<Record<string, readonly WpFieldError[]>>>({})
  const topErrors = shallowRef<readonly WpFieldError[]>([])
  const capability = shallowRef<readonly WorkPlanCapabilityFinding[] | null>(null)
  const externalRevision = ref<number | null>(null)
  const pendingQuantity = shallowRef<WorkPlanPendingQuantity | null>(null)
  const notice = shallowRef<WorkPlanEditorNotice | null>(null)
  /** Old row id → the id it has now (a new card named by a save). */
  const aliases = new Map<string, string>()
  const sessionStops = new Set<() => void>()
  const alive = ref(true)
  const planEpoch = ref(0)
  // Request order: only the latest read may set the view.
  let generation = 0
  /** The Flow's live AI run read on its way (one at a time). */
  let activityRead: Promise<unknown> | null = null
  const deployStatus = shallowRef<WorkPlanDeployStatus | null>(null)
  const omitting = ref<string | null>(null)
  /** The rows whose 「지시 생략」 was saved but not deployed: each one's button offers 「전개 재시도」. */
  const omitRetry = shallowRef<ReadonlySet<string>>(new Set())
  // Request order of the deploy status reads: only the latest sets what is shown.
  let deploySeq = 0

  const aiRunning = computed(() => {
    const groupId = options.groupId()
    return groupId !== null && documentAiActivityOf(groupId)?.active === true
  })

  const lockReason = computed<WorkPlanLockReason | null>(() =>
    workPlanLockReason({
      external: options.external(),
      aiRunning: aiRunning.value,
      uploadGuarded: documentIoGuarded(options.docId()),
      editable: view.value?.editable ?? true,
      editLockedReason: view.value?.editLockedReason ?? null,
      refused: refused.value,
    }),
  )
  const inputsLocked = computed(
    () =>
      phase.value !== 'ready' ||
      draft.value === null ||
      lockReason.value !== null ||
      saving.value ||
      reloading.value ||
      capability.value !== null,
  )
  const canSave = computed(() => dirty.value && !inputsLocked.value && !options.stepPending?.())
  const unassigned = computed(() => (draft.value ? unassignedSteps(draft.value) : 0))
  const started = () => view.value?.startedCardIds ?? new Set<string>()

  function clearSaveState(): void {
    failure.value = null
    rowErrors.value = {}
    topErrors.value = []
  }

  function readActivity(): void {
    const groupId = options.groupId()
    if (groupId === null || activityRead !== null) return
    activityRead = readDocumentAiActivity(groupId).finally(() => {
      activityRead = null
    })
  }

  function take(next: WorkPlanEditView): void {
    if (draft.value && JSON.stringify(bodyOf(draft.value)) !== JSON.stringify(next.body)) planEpoch.value += 1
    view.value = next
    // A plan read anew may be editable again; a discarded Flow is not (the read does not say so).
    if (refused.value !== 'disposed') refused.value = null
    draft.value = draftOf(pairedDesignBody(next.body))
    dirty.value = false
    capability.value = null
    externalRevision.value = null
    pendingQuantity.value = null
    clearSaveState()
    phase.value = 'ready'
    loadError.value = null
    unreadableRaw.value = null
  }

  async function load(): Promise<boolean> {
    const seq = ++generation
    // Over a shown plan the inputs wait for the answer: it replaces the draft, and what is typed meanwhile with it.
    if (view.value === null) phase.value = 'loading'
    else reloading.value = true
    readActivity()
    try {
      const answer = await workPlanEditRead(options.docId())
      if (seq !== generation || !alive.value) return false
      take(toWorkPlanEditView(answer))
      void readDeployStatus()
      return true
    } catch (error) {
      if (seq !== generation || !alive.value) return false
      const kind = workPlanErrorOf(error).kind
      view.value = null
      draft.value = null
      dirty.value = false
      loadError.value = kind
      if (kind === 'unreadable') {
        const raw = (error as { response?: { data?: { raw?: unknown } } })?.response?.data?.raw
        unreadableRaw.value = typeof raw === 'string' ? raw : null
        phase.value = 'unreadable'
      } else phase.value = 'error'
      return false
    } finally {
      // A later read (or the window closing) owns the flag now.
      if (seq === generation) reloading.value = false
    }
  }

  async function refresh(): Promise<void> {
    readActivity()
    // A read again is on its way and brings the server's state itself.
    if (reloading.value) return
    if (phase.value !== 'ready' || saving.value) {
      if (phase.value !== 'ready' && phase.value !== 'loading') await load()
      return
    }
    if (!dirty.value && !options.stepPending?.()) {
      const seq = ++generation
      try {
        const answer = await workPlanEditRead(options.docId())
        // A person may have started editing while the read was on its way: their edit wins.
        if (seq !== generation || !alive.value || dirty.value || options.stepPending?.() || saving.value) return
        take(toWorkPlanEditView(answer))
      } catch {
        // A failed background read keeps what is shown; the next signal reads again.
      }
      return
    }
    await readVerdict()
  }

  /** Reads the server's lock verdict and revision again; the window's body, draft and base revision stay. */
  async function readVerdict(): Promise<void> {
    const seq = generation
    try {
      const fresh = toWorkPlanEditView(await workPlanEditRead(options.docId()))
      const current = view.value
      if (seq !== generation || !alive.value || current === null) return
      // The new lock verdict and started cards count now; the body and its revision stay the window's.
      view.value = { ...fresh, revisionNo: current.revisionNo, body: current.body }
      externalRevision.value = fresh.revisionNo !== current.revisionNo ? fresh.revisionNo : null
    } catch {
      // as above
    }
  }

  async function send(acks: readonly string[]): Promise<'saved' | 'capability' | 'failed' | 'blocked'> {
    const current = view.value
    const sending = draft.value
    if (!current || !sending || saving.value || reloading.value || phase.value !== 'ready' || lockReason.value !== null || options.stepPending?.()) return 'blocked'
    let reread = false
    saving.value = true
    clearSaveState()
    notice.value = null
    // A background read started before the save must not land after it.
    generation += 1
    try {
      const answer = await workPlanSave(options.docId(), { baseRevisionNo: current.revisionNo, body: bodyOf(sending), capabilityWarningAcks: acks })
      if (!alive.value) return 'saved'
      const revisionNo = typeof answer.revision_no === 'number' ? answer.revision_no : current.revisionNo + 1
      const savedBody = editBodyOf(answer.body ?? bodyOf(sending))
      const { draft: next, renamed } = savedDraft(sending, savedBody)
      for (const [from, to] of renamed) aliases.set(from, to)
      view.value = {
        ...current,
        revisionNo,
        title: typeof answer.title === 'string' && answer.title ? answer.title : current.title,
        docReviewStatus: typeof answer.doc_review_status === 'string' ? answer.doc_review_status : current.docReviewStatus,
        body: savedBody,
      }
      draft.value = next
      dirty.value = false
      capability.value = null
      externalRevision.value = null
      const left = unassignedSteps(next)
      notice.value = left > 0
        ? { key: 'saved_unassigned', params: { revision: revisionNo, n: left }, tone: 'warning' }
        : { key: 'saved', params: { revision: revisionNo }, tone: 'success' }
      options.saved?.(revisionNo)
      // 「지시 생략」 reads the status itself, for its deploy.
      if (omitting.value === null) void readDeployStatus()
      return 'saved'
    } catch (error) {
      if (!alive.value) return 'failed'
      const reason = workPlanSaveFailureOf(error)
      if (reason.kind === 'capability') {
        capability.value = reason.findings
        return 'capability'
      }
      capability.value = null
      failure.value = reason
      if (reason.kind === 'validation' && draft.value) {
        const pinned = errorsByRow(draft.value, reason.errors)
        rowErrors.value = pinned.rows
        topErrors.value = pinned.top
      }
      if (reason.kind === 'ai_running') readActivity()
      if (reason.kind === 'not_editable' || reason.kind === 'disposed') {
        // The server's verdict changed under the window: locked from now on (nothing more is sent), and the lock
        // state is read again once the save is over (the edits stay).
        refused.value = reason.kind
        reread = true
      }
      return 'failed'
    } finally {
      saving.value = false
      if (reread && alive.value) void readVerdict()
    }
  }

  const save = () => send([])
  const confirmCapability = () => (capability.value ? send(capability.value.map((finding) => finding.stepKey)) : Promise.resolve('blocked' as const))
  function cancelCapability(): void {
    capability.value = null
  }

  function edit(change: (current: WorkPlanDraft) => WorkPlanDraft): boolean {
    const current = draft.value
    if (!current || inputsLocked.value) return false
    const next = change(current)
    if (next === current) return false
    draft.value = next
    dirty.value = true
    return true
  }

  function setQuantity(type: string, next: number): void {
    const current = draft.value
    if (!current || inputsLocked.value) return
    const plan = planQuantity(current, type, next, started())
    if (plan.status === 'blocked_started') {
      notice.value = { key: 'quantity_blocked_started', params: { type }, tone: 'warning' }
      return
    }
    if (plan.status === 'unsupported') {
      notice.value = { key: 'quantity_unsupported', params: { type }, tone: 'warning' }
      return
    }
    if (plan.status !== 'ok') return
    if (plan.valueSteps > 0) {
      pendingQuantity.value = { type, count: plan.count, valueSteps: plan.valueSteps }
      return
    }
    edit((d) => applyQuantity(d, type, plan.count, started()))
  }

  function confirmQuantity(): void {
    const pending = pendingQuantity.value
    pendingQuantity.value = null
    if (pending) edit((d) => applyQuantity(d, pending.type, pending.count, started()))
  }

  function cancelQuantity(): void {
    pendingQuantity.value = null
  }

  function moveCard(from: number, to: number): void {
    const current = draft.value
    if (!current || inputsLocked.value) return
    const result = moveShownCard(current, from, to, started())
    if (result.status === 'moved') edit(() => result.draft)
    else if (result.status === 'started') notice.value = { key: 'move_started', tone: 'warning' }
    else if (result.status === 'before_started') notice.value = { key: 'move_before_started', tone: 'warning' }
  }

  function canMoveCardBy(position: number, delta: number): boolean {
    return !!draft.value && !inputsLocked.value && canMoveShownCardBy(draft.value, position, delta, started())
  }

  function moveCardBy(position: number, delta: number): void {
    if (canMoveCardBy(position, delta)) moveCard(position, position + delta)
  }

  const nameOf = (providerId: string, snapshot: string | null = null) =>
    view.value ? providerName(view.value, providerId, snapshot) : snapshot ?? providerId

  function setStepProvider(rowId: string, providerId: string | null): void {
    edit((d) => setStepProviderIn(d, rowId, providerId, providerId ? nameOf(providerId) : null))
  }

  function setInstructionMode(rowId: string, omitted: boolean): void {
    const current = draft.value
    const row = current && rowOf(current, rowId)
    if (!current || !row || started().has(current.cards[row.cardIndex].id)) return
    edit((value) => setInstructionModeIn(value, rowId, omitted))
  }

  function setStepNote(rowId: string, note: string): void {
    edit((d) => setStepNoteIn(d, rowId, note))
  }

  const reviewCountChoices = (): readonly number[] => view.value?.reviewCountChoices ?? [0]

  function setStepReview(rowId: string, reviewCount: number, reviewerProviderId: string | null): void {
    const current = draft.value
    const row = current ? rowOf(current, rowId) : null
    // Only what the picker offers: the server's counts, or the row's own count kept as it is.
    if (!row || (!reviewCountChoices().includes(reviewCount) && reviewCount !== (row.step.review_count ?? 0))) return
    edit((d) => setStepReviewIn(d, rowId, reviewCount, reviewerProviderId, (id) => nameOf(id)))
  }

  function reviewerOptions(emptyLabel: string, unavailable: (name: string) => string): SelectOption[] {
    return pickerOptions((draft.value?.cards ?? []).flatMap((card) => card.steps.map((step) => step.reviewer_provider_id)), emptyLabel, unavailable)
  }

  function setDefaultProvider(providerId: string | null): void {
    edit((d) => setDefaultProviderIn(d, providerId))
  }

  function setDefaultNote(note: string): void {
    edit((d) => setDefaultNoteIn(d, note))
  }

  function applyDefaults(): void {
    if (edit((d) => applyDefaultsTo(d, (id) => nameOf(id)))) notice.value = { key: 'applied_defaults', tone: 'success' }
  }

  function pickerOptions(used: readonly (string | null)[], emptyLabel: string, unavailable: (name: string) => string): SelectOption[] {
    return view.value ? providerOptions({ registered: view.value.registered, body: draft.value?.body ?? view.value.body }, used, emptyLabel, unavailable) : [{ value: '', label: emptyLabel }]
  }

  const resolve = (rowId: string): string => {
    let id = rowId
    for (let hops = 0; aliases.has(id) && hops < 8; hops++) id = aliases.get(id) as string
    return id
  }

  function stepTarget(rowId: string): WorkPlanStepEditTarget | null {
    const current = draft.value
    if (!current || !alive.value) return null
    const row = rowOf(current, resolve(rowId))
    if (!row) return null
    const head = current.cards[row.cardIndex].steps[0]
    const shown = shownCardsOf(current).flatMap((card) => card.rows).find((item) => item.rowId === row.rowId)
    return {
      docId: options.docId(),
      rowId: row.rowId,
      stepKey: row.step.key,
      stepNo: shown?.no ?? row.no,
      type: row.step.type,
      pairRole: row.step.pair_role,
      cardType: cardType(current.cards[row.cardIndex]),
      cardUnit: unitOf(current.body, cardType(current.cards[row.cardIndex])),
      cardOrdinal: head.ordinal,
    }
  }

  function stepSession(rowId: string): WorkPlanStepEditSession {
    const epoch = planEpoch.value
    const valid = ref(true)
    // Deletion is permanent for this session, even if a quantity restore reuses the card id.
    // A server reload replacing the body cannot rebind legacy key-based identities either.
    const stop = watch(() => [planEpoch.value, stepTarget(rowId)] as const, ([nextEpoch, target]) => {
      if (nextEpoch !== epoch || target === null) valid.value = false
    }, { flush: 'sync' })
    sessionStops.add(stop)
    return {
      target: () => valid.value ? stepTarget(rowId) : null,
      snapshot: () => (draft.value && alive.value && valid.value ? stepDraftOf(draft.value, resolve(rowId)) : null),
      apply(values) {
        if (!alive.value || !draft.value || !valid.value) return 'gone'
        if (inputsLocked.value) return 'locked'
        const result = applyStepDraft(draft.value, resolve(rowId), values)
        if (result.status !== 'applied') return result.status
        if (result.draft !== draft.value) {
          draft.value = result.draft
          dirty.value = true
        }
        return 'applied'
      },
      locked: () => !alive.value || inputsLocked.value,
      lockReason: () => lockReason.value,
      alive: () => alive.value,
      preInstructionMaxChars: () => view.value?.preInstructionMaxChars ?? 20000,
      attachmentMaxBytes: () => view.value?.attachmentMaxBytes ?? 0,
      refreshLocks: (reason) => {
        if (reason) refused.value = reason
        readActivity()
        void readVerdict()
      },
      attachmentStepKey: () => {
        if (!valid.value || !alive.value || !draft.value || !view.value) return null
        const row = rowOf(draft.value, resolve(rowId))
        if (!row || !instructionEligible(row.step)) return null
        const card = draft.value.cards[row.cardIndex]
        return view.value.body.steps.find((step) => step.card_id === card.id
          && (step.pair_role === row.step.pair_role
            || (row.step.pair_role === 'result' && step.pair_role === 'single')))?.key ?? null
      },
    }
  }

  /** Reads the plan's deploy status; the latest read sets what is shown. Resolves to this read's answer (null: it failed). */
  async function readDeployStatus(): Promise<WorkPlanDeployStatus | null> {
    const seq = ++deploySeq
    try {
      const status = await workPlanDeployStatus(options.docId())
      if (seq === deploySeq && alive.value) deployStatus.value = status
      return alive.value ? status : null
    } catch {
      if (seq === deploySeq && alive.value) deployStatus.value = null
      return null
    }
  }

  const retrying = (id: string) => [...omitRetry.value].some((stored) => resolve(stored) === id)

  function setRetry(id: string, on: boolean): void {
    const next = new Set([...omitRetry.value].map(resolve))
    if (on) next.add(id)
    else next.delete(id)
    omitRetry.value = next
  }

  /** Drops the 「전개 재시도」 of every row whose card this deploy answer settled (or whose row is gone). */
  function settleRetries(cards: readonly WorkPlanOmitDeployCard[], settled: (verdict: WorkPlanOmitCardVerdict) => boolean): void {
    const current = draft.value
    if (omitRetry.value.size === 0) return
    const next = new Set<string>()
    for (const stored of omitRetry.value) {
      const id = resolve(stored)
      const row = current ? rowOf(current, id) : null
      if (current && row && !settled(omitCardVerdict(cards, current.cards[row.cardIndex].id))) next.add(id)
    }
    omitRetry.value = next
  }

  // A row gone from the plan (a quantity, an import, a read again) takes its 「전개 재시도」 with it.
  watch(draft, (current) => {
    if (omitRetry.value.size === 0) return
    const kept = [...omitRetry.value].map(resolve).filter((id) => current !== null && rowOf(current, id) !== null)
    if (kept.length !== omitRetry.value.size) omitRetry.value = new Set(kept)
  })

  function omitState(rowId: string): WorkPlanOmitState {
    const current = draft.value
    const id = resolve(rowId)
    const row = current && alive.value ? rowOf(current, id) : null
    if (!row) return { visible: false, enabled: false, action: 'omit', reason: null }
    return workPlanOmitState({
      step: row.step,
      lockReason: lockReason.value,
      inputsLocked: inputsLocked.value,
      dirty: dirty.value || options.stepPending?.() === true,
      stepOpen: options.stepOpen?.(id) === true,
      deployable: deployStatus.value?.deployable === true,
      busy: omitting.value !== null,
      retry: retrying(id),
    })
  }

  async function omitStep(rowId: string): Promise<WorkPlanOmitOutcome> {
    const state = omitState(rowId)
    if (!state.enabled) return 'blocked'
    const id = resolve(rowId)
    omitting.value = id
    notice.value = null
    try {
      if (state.action === 'omit') {
        const before = draft.value as WorkPlanDraft
        const applied = applyStepDraft(before, id, { preInstructionText: WORK_PLAN_OMIT_TEXT, preInstructionAttachment: null })
        if (applied.status !== 'applied' || applied.draft === before) return 'blocked'
        draft.value = applied.draft
        dirty.value = true
        const saved = await send([])
        if (saved !== 'saved') {
          if (!alive.value) return 'save_failed'
          // The row is empty again and the window as clean as it was; the save's failure (shown as for [저장]) says why.
          const findings = capability.value
          draft.value = before
          dirty.value = false
          if (saved === 'capability') {
            // Not confirmed on the person's behalf: a failed save.
            capability.value = null
            failure.value = { kind: 'capability', status: 422, message: null, errors: [], findings: findings ?? [], updatedBy: null, updatedAt: null }
          }
          return saved === 'blocked' ? 'blocked' : 'save_failed'
        }
      }
      if (!alive.value) return 'deploy_failed'
      return await deployOmitted(resolve(id))
    } finally {
      omitting.value = null
    }
  }

  /** The deploy of a saved 「지시 생략」: the saved revision, the mode by the deployed cards, the preview's baseline. */
  async function deployOmitted(id: string): Promise<WorkPlanOmitOutcome> {
    const docId = options.docId()
    const current = draft.value
    const row = current ? rowOf(current, id) : null
    const cardId = current && row ? current.cards[row.cardIndex].id : null
    const revisionNo = view.value?.revisionNo ?? null
    const failed = (detail: string): WorkPlanOmitOutcome => {
      setRetry(id, true)
      notice.value = { key: 'omit_deploy_failed', tone: 'warning', detail }
      return 'deploy_failed'
    }
    if (cardId === null || revisionNo === null) return failed('deploy_failure.other')
    try {
      const status = await readDeployStatus()
      if (!alive.value) return 'deploy_failed'
      if (status === null) return failed('deploy_failure.status')
      const preview = await workPlanDeployPreview(docId, status.deployedCount > 0 ? 'add' : 'first', revisionNo)
      if (!alive.value) return 'deploy_failed'
      if (preview.blockers.length > 0) return failed('deploy_failure.blocked')
      if (preview.noChange || !preview.executable) {
        // Nothing runs; the preview still says why for the row's card (its line kept, another type, …).
        const verdict = omitCardVerdict(preview.cards, cardId)
        settleRetries(preview.cards, (other) => other !== 'written' && other !== 'unconfirmed')
        setRetry(id, false)
        if (verdict === 'written' || verdict === 'unconfirmed') {
          notice.value = { key: 'omit_no_change', tone: 'warning' }
          return 'no_change'
        }
        notice.value = { key: OMIT_SETTLED[verdict].key, tone: 'warning' }
        return OMIT_SETTLED[verdict].outcome
      }
      const result = await workPlanDeployExecute(docId, preview)
      if (!alive.value) return 'unconfirmed'
      void readDeployStatus()
      // The request went through; whether the row's instruction was written is its card's own entry.
      settleRetries(result.cards, (other) => other !== 'unconfirmed')
      const verdict = omitCardVerdict(result.cards, cardId)
      if (verdict === 'unconfirmed') {
        setRetry(id, true)
        notice.value = { key: 'omit_unconfirmed', tone: 'warning' }
        return 'unconfirmed'
      }
      if (verdict !== 'written') {
        notice.value = { key: OMIT_SETTLED[verdict].key, tone: 'warning' }
        return OMIT_SETTLED[verdict].outcome
      }
      notice.value = { key: 'omitted', tone: 'success' }
      return 'omitted'
    } catch (error) {
      if (!alive.value) return 'deploy_failed'
      return failed(`deploy_failure.${omitDeployFailure(workPlanDeployFailureOf(error).kind)}`)
    }
  }

  function exportJson(): string | null {
    return draft.value ? workPlanJsonText(draft.value) : null
  }

  function importJson(text: string): WorkPlanJsonImportResult {
    const current = draft.value
    if (!current || inputsLocked.value) return { status: 'locked' }
    // A Step window's edits are for a row of this plan: they are saved or dropped first.
    if (options.stepPending?.()) return { status: 'step_pending' }
    const read = readWorkPlanJson(text, current, started())
    if (!read.ok) return { status: 'invalid', issues: read.issues }
    const next = draftOf(pairedDesignBody(read.body))
    if (JSON.stringify(bodyOf(next)) === JSON.stringify(bodyOf(current))) {
      notice.value = { key: 'imported_unchanged', tone: 'success' }
      return { status: 'unchanged' }
    }
    draft.value = next
    dirty.value = true
    pendingQuantity.value = null
    clearSaveState()
    // Another plan: the Step windows open on the replaced one lose their row.
    planEpoch.value += 1
    notice.value = { key: 'imported', tone: 'success' }
    return { status: 'imported' }
  }

  // An upload of this document waits while the window holds unsaved edits; a finished one is read again here.
  const unregister = registerDocumentEditEntry({
    related: () => [options.docId()],
    editor: () => (phase.value === 'ready' ? 'ready' : phase.value === 'loading' ? 'loading' : 'error'),
    dirty: () => dirty.value || options.stepPending?.() === true,
    saving: () => saving.value,
    onGuard: () => {},
    onRelease: () => {},
    reflect: async () => {
      if (dirty.value || options.stepPending?.() || saving.value) return 'skipped'
      return (await load()) ? 'ok' : 'failed'
    },
  })

  function dispose(): void {
    if (!alive.value) return
    alive.value = false
    for (const stop of sessionStops) stop()
    sessionStops.clear()
    generation += 1
    deploySeq += 1
    unregister()
  }

  onScopeDispose(dispose)

  return {
    phase,
    loadError,
    unreadableRaw,
    view,
    draft,
    dirty,
    saving,
    reloading,
    lockReason,
    inputsLocked,
    canSave,
    failure,
    rowErrors,
    topErrors,
    capability,
    externalRevision,
    pendingQuantity,
    notice,
    unassigned,
    load,
    refresh,
    refreshActivity: readActivity,
    save,
    confirmCapability,
    cancelCapability,
    setQuantity,
    confirmQuantity,
    cancelQuantity,
    moveCard,
    moveCardBy,
    canMoveCardBy,
    cardStarted: (cardId) => started().has(cardId),
    setStepProvider,
    setStepNote,
    setInstructionMode,
    setStepReview,
    reviewCountChoices,
    reviewerOptions,
    setDefaultProvider,
    setDefaultNote,
    applyDefaults,
    providerOptions: pickerOptions,
    providerName: nameOf,
    stepTarget,
    stepSession,
    exportJson,
    importJson,
    deployStatus,
    omitting,
    omitState,
    omitStep,
    dismissNotice: () => {
      notice.value = null
    },
    dispose,
  }
}
