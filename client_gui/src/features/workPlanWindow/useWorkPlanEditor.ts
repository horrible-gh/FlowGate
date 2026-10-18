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
// - The Step window (T#2) edits one row through `stepSession(rowId)`: a copy of the row's review / pre-instruction
//   values, and `apply` that puts its values on that row only (parent dirty; the server PUT stays the parent's).
import { computed, onScopeDispose, ref, shallowRef, type ComputedRef, type Ref, type ShallowRef } from 'vue'
import {
  workPlanEditRead,
  workPlanErrorOf,
  workPlanSave,
  workPlanSaveFailureOf,
  type WorkPlanCapabilityFinding,
  type WorkPlanErrorKind,
  type WorkPlanSaveFailure,
} from '@/features/document'
import type { WpFieldError } from '@shared/workPlanErrors'
import type { SelectOption } from '@/components/ui/Select.vue'
import { documentAiActivityOf, readDocumentAiActivity } from '@/features/documentWindow/documentAiWindows'
import { documentIoGuarded, registerDocumentEditEntry } from '@/features/documentWindow/documentEditRegistry'
import {
  applyDefaults as applyDefaultsTo,
  applyQuantity,
  applyStepDraft,
  bodyOf,
  canMoveCardBy as canMoveBy,
  draftOf,
  editBodyOf,
  errorsByRow,
  moveCard as moveCardIn,
  planQuantity,
  providerName,
  providerOptions,
  rowOf,
  savedDraft,
  setDefaultNote as setDefaultNoteIn,
  setDefaultProvider as setDefaultProviderIn,
  setStepNote as setStepNoteIn,
  setStepProvider as setStepProviderIn,
  stepDraftOf,
  toWorkPlanEditView,
  unassignedSteps,
  workPlanLockReason,
  type WorkPlanDraft,
  type WorkPlanEditView,
  type WorkPlanLockReason,
  type WorkPlanPairRole,
  type WorkPlanRefusal,
  type WorkPlanStepDraft,
} from './workPlanEditModel'

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
  readonly params?: Readonly<Record<string, string | number>>
  readonly tone: 'success' | 'warning'
}

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
  readonly stepKey: string
  /** 1-based position in the plan. */
  readonly stepNo: number
  readonly type: string
  readonly pairRole: WorkPlanPairRole
}

export type WorkPlanStepApplyStatus = 'applied' | 'gone' | 'locked' | 'not_eligible'

/** What the parent window gives a Step window (T#2) for one row. Read at call time. */
export interface WorkPlanStepEditSession {
  /** The row now, or null when it is gone (its card removed, the plan read again without it, the window closed). */
  target(): WorkPlanStepEditTarget | null
  /** A copy of the row's review / pre-instruction values, or null (gone / locked Step). */
  snapshot(): WorkPlanStepDraft | null
  /** Puts the values on this row only; the parent becomes dirty and saves them with its [저장]. */
  apply(values: WorkPlanStepDraft): WorkPlanStepApplyStatus
  /** The parent cannot be changed now (any lock reason, a save running, a capability confirmation pending). */
  locked(): boolean
  lockReason(): WorkPlanLockReason | null
  /** The parent window is still open. */
  alive(): boolean
  reviewCountChoices(): readonly number[]
  preInstructionMaxChars(): number
  attachmentMaxBytes(): number
  /** The reviewer picker's options (empty = the project's default reviewer at run time). */
  reviewerOptions(emptyLabel: string, unavailable: (name: string) => string): SelectOption[]
}

export interface WorkPlanEditorOptions {
  docId(): string
  /** The document's Flow (v0.1 group id) whose AI run locks the plan, or null. */
  groupId(): string | null
  /** The opener's verdict: read only (ownership conflict / revision view) or the Flow's run lock; null = none. */
  external(): 'read_only' | 'run_locked' | null
  /** Told after every successful save. */
  saved?(revisionNo: number): void
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
  moveCard(from: number, to: number): void
  moveCardBy(index: number, delta: number): void
  canMoveCardBy(index: number, delta: number): boolean
  cardStarted(cardId: string): boolean
  setStepProvider(rowId: string, providerId: string | null): void
  setStepNote(rowId: string, note: string): void
  setDefaultProvider(providerId: string | null): void
  setDefaultNote(note: string): void
  applyDefaults(): void
  providerOptions(used: readonly (string | null)[], emptyLabel: string, unavailable: (name: string) => string): SelectOption[]
  providerName(providerId: string, snapshot?: string | null): string
  stepTarget(rowId: string): WorkPlanStepEditTarget | null
  stepSession(rowId: string): WorkPlanStepEditSession
  dismissNotice(): void
  /** The window is closing: sessions report not alive, late answers are dropped. */
  dispose(): void
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
  let alive = true
  // Request order: only the latest read may set the view.
  let generation = 0
  /** The Flow's live AI run read on its way (one at a time). */
  let activityRead: Promise<unknown> | null = null

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
  const canSave = computed(() => dirty.value && !inputsLocked.value)
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
    view.value = next
    // A plan read anew may be editable again; a discarded Flow is not (the read does not say so).
    if (refused.value !== 'disposed') refused.value = null
    draft.value = draftOf(next.body)
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
      if (seq !== generation || !alive) return false
      take(toWorkPlanEditView(answer))
      return true
    } catch (error) {
      if (seq !== generation || !alive) return false
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
    if (!dirty.value) {
      const seq = ++generation
      try {
        const answer = await workPlanEditRead(options.docId())
        // A person may have started editing while the read was on its way: their edit wins.
        if (seq !== generation || !alive || dirty.value || saving.value) return
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
      if (seq !== generation || !alive || current === null) return
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
    if (!current || !sending || saving.value || reloading.value || phase.value !== 'ready' || lockReason.value !== null) return 'blocked'
    let reread = false
    saving.value = true
    clearSaveState()
    notice.value = null
    // A background read started before the save must not land after it.
    generation += 1
    try {
      const answer = await workPlanSave(options.docId(), { baseRevisionNo: current.revisionNo, body: bodyOf(sending), capabilityWarningAcks: acks })
      if (!alive) return 'saved'
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
      return 'saved'
    } catch (error) {
      if (!alive) return 'failed'
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
      if (reread && alive) void readVerdict()
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
    const result = moveCardIn(current, from, to, started())
    if (result.status === 'moved') edit(() => result.draft)
    else if (result.status === 'started') notice.value = { key: 'move_started', tone: 'warning' }
    else if (result.status === 'before_started') notice.value = { key: 'move_before_started', tone: 'warning' }
  }

  function canMoveCardBy(index: number, delta: number): boolean {
    return !!draft.value && !inputsLocked.value && canMoveBy(draft.value, index, delta, started())
  }

  function moveCardBy(index: number, delta: number): void {
    if (canMoveCardBy(index, delta)) moveCard(index, index + delta)
  }

  const nameOf = (providerId: string, snapshot: string | null = null) =>
    view.value ? providerName(view.value, providerId, snapshot) : snapshot ?? providerId

  function setStepProvider(rowId: string, providerId: string | null): void {
    edit((d) => setStepProviderIn(d, rowId, providerId, providerId ? nameOf(providerId) : null))
  }

  function setStepNote(rowId: string, note: string): void {
    edit((d) => setStepNoteIn(d, rowId, note))
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
    if (!current || !alive) return null
    const row = rowOf(current, resolve(rowId))
    if (!row) return null
    return { docId: options.docId(), rowId: row.rowId, stepKey: row.step.key, stepNo: row.no, type: row.step.type, pairRole: row.step.pair_role }
  }

  function stepSession(rowId: string): WorkPlanStepEditSession {
    return {
      target: () => stepTarget(rowId),
      snapshot: () => (draft.value && alive ? stepDraftOf(draft.value, resolve(rowId)) : null),
      apply(values) {
        if (!alive || !draft.value) return 'gone'
        if (inputsLocked.value) return 'locked'
        const result = applyStepDraft(draft.value, resolve(rowId), values, (id) => nameOf(id))
        if (result.status !== 'applied') return result.status
        draft.value = result.draft
        dirty.value = true
        return 'applied'
      },
      locked: () => !alive || inputsLocked.value,
      lockReason: () => lockReason.value,
      alive: () => alive,
      reviewCountChoices: () => view.value?.reviewCountChoices ?? [0],
      preInstructionMaxChars: () => view.value?.preInstructionMaxChars ?? 20000,
      attachmentMaxBytes: () => view.value?.attachmentMaxBytes ?? 0,
      reviewerOptions: (emptyLabel, unavailable) =>
        pickerOptions((draft.value?.cards ?? []).flatMap((card) => card.steps.map((step) => step.reviewer_provider_id)), emptyLabel, unavailable),
    }
  }

  // An upload of this document waits while the window holds unsaved edits; a finished one is read again here.
  const unregister = registerDocumentEditEntry({
    related: () => [options.docId()],
    editor: () => (phase.value === 'ready' ? 'ready' : phase.value === 'loading' ? 'loading' : 'error'),
    dirty: () => dirty.value,
    saving: () => saving.value,
    onGuard: () => {},
    onRelease: () => {},
    reflect: async () => {
      if (dirty.value || saving.value) return 'skipped'
      return (await load()) ? 'ok' : 'failed'
    },
  })

  function dispose(): void {
    if (!alive) return
    alive = false
    generation += 1
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
    setDefaultProvider,
    setDefaultNote,
    applyDefaults,
    providerOptions: pickerOptions,
    providerName: nameOf,
    stepTarget,
    stepSession,
    dismissNotice: () => {
      notice.value = null
    },
    dispose,
  }
}
