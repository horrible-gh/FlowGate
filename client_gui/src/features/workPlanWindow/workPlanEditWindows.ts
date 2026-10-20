// flowgate.v02.0072 T#1 — the work plan edit windows of this tab, kept the way the document windows are
// (documentWindows.ts / slotWriteWindows.ts): module state, one window per work plan document, opening it again brings
// it back (and, when it was minimized, restores it). flowgate.v02.0072 T#3: the WP card's 「작업계획」 line opens it (the
// Inspector's openDocument.ts openWorkPlan); the App Shell mounts WorkPlanEditWindows once (through DocumentWindows).
//
// The Step window boundary (T#2). A Step row's ✎ does not open anything inside this window (no inline drawer): the
// window hands the row to the Step window's handler, registered here by the Step window feature
// (setWorkPlanStepEditHandler). The handler gets the row's target and a session bound to the parent window's editor
// (useWorkPlanEditor.stepSession): read a copy of the row's pre-instruction values (0073 T#2: not its review), put edited values back on
// that row only, and ask whether the parent is locked or still open. The final server save stays the parent's [저장].
// While no handler is registered, ✎ is shown disabled with its reason.
//
// flowgate.v02.0074 T#2 (D#1 §6.3 / §7.5) — the document [업로드] of a work plan is this window's [JSON 가져오기]: the
// document I/O coordinator opens the plan's window (an open one is reused) asking for the import window
// (openWorkPlanEditImport, initialMode 'import'); nothing is saved until the window's [저장] (one PUT with
// base_revision_no). A window opened from there gets its opener's verdict and save callback through the opener the
// Inspector registers (setWorkPlanEditOpener); without one the window is run-locked, as workPlanFlowLock without a reader.
// Each window tells whether its draft holds unsaved edits (dirty), so a download of the plan can say they are not in it.
import { reactive, shallowRef } from 'vue'
import type { WorkPlanStepEditSession, WorkPlanStepEditTarget } from './useWorkPlanEditor'

/** What the window edits and whom it tells. */
export interface WorkPlanEditRequest {
  readonly docId: string
  /** The project whose events the window hears (external changes), when known. */
  readonly projectId: string | null
  /** The document's Flow (v0.1 group id); derived from the document id when left out. */
  readonly groupId?: string | null
  /** The document's title, shown until the plan is read. */
  readonly title?: string | null
  /** The opener's read-only verdict, read reactively (ownership conflict / revision view, the Flow's run lock). */
  readonly external?: () => 'read_only' | 'run_locked' | null
  /** Told after every successful save (the opener reads its line again). */
  readonly saved?: (revisionNo: number) => void
  /** flowgate.v02.0074 T#2: 'import' opens [JSON 가져오기] once the plan is read (once per request); absent or null: as before. */
  readonly initialMode?: 'import' | null
}

/** The opener's part of a request for a window opened from outside the Inspector's work plan line (T#2). */
export type WorkPlanEditOpener = (docId: string, flowId: string) => Pick<WorkPlanEditRequest, 'external' | 'saved'>

export interface WorkPlanEditWindowState {
  readonly docId: string
  request: WorkPlanEditRequest
  minimized: boolean
  /** flowgate.v02.0074 T#2: the window's draft holds unsaved edits (the window keeps it in step). */
  dirty: boolean
  readonly x: number
  readonly y: number
  /** Bumped when the window is asked for again (the window comes to the front). */
  raise: number
}

/** The Step window's side of the boundary (T#2). */
export interface WorkPlanStepEditHandler {
  /** Open (or bring back) the Step window of the row. */
  open(target: WorkPlanStepEditTarget, session: WorkPlanStepEditSession): void
  /** Step windows of the document hold edits not yet applied to the parent (asked before the parent closes). */
  pending?(docId: string): boolean
  /** The parent window of the document closed: its Step windows close too. */
  parentClosed?(docId: string): void
  /** A Step window of that row is open (flowgate.v02.0074 T#5: the row's 「지시 생략」 waits for it). */
  openRow?(docId: string, rowId: string): boolean
}

const CASCADE_STEPS = 6
const CASCADE_PX = 28
const ORIGIN_X = 200
const ORIGIN_Y = 88

const windows = reactive<WorkPlanEditWindowState[]>([])
let cascade = 0
const stepHandler = shallowRef<WorkPlanStepEditHandler | null>(null)
const editOpener = shallowRef<WorkPlanEditOpener | null>(null)

/** The Flow (v0.1 group id) of a document id `<project>.<module>.<group>.<nnnn>-<TYPE>`, or null. */
export function workPlanGroupOf(docId: string): string | null {
  const parts = docId.split('.')
  return parts.length >= 4 ? parts.slice(0, 3).join('.') : null
}

export function workPlanEditWindowList(): readonly WorkPlanEditWindowState[] {
  return windows
}

export function workPlanEditWindowOf(docId: string): WorkPlanEditWindowState | undefined {
  return windows.find((win) => win.docId === docId)
}

/** Opens the edit window of a work plan document; one already open is brought back with the newest request. */
export function openWorkPlanEditWindow(request: WorkPlanEditRequest): void {
  const open = workPlanEditWindowOf(request.docId)
  if (open) {
    open.request = request
    open.minimized = false
    open.raise += 1
    return
  }
  const offset = (cascade++ % CASCADE_STEPS) * CASCADE_PX
  windows.push({ docId: request.docId, request, minimized: false, dirty: false, x: ORIGIN_X + offset, y: ORIGIN_Y + offset, raise: 0 })
}

export function setWorkPlanEditWindowMinimized(docId: string, minimized: boolean): void {
  const open = workPlanEditWindowOf(docId)
  if (open) open.minimized = minimized
}

/** flowgate.v02.0074 T#2 — the window tells whether its draft holds unsaved edits. */
export function setWorkPlanEditWindowDirty(docId: string, dirty: boolean): void {
  const open = workPlanEditWindowOf(docId)
  if (open && open.dirty !== dirty) open.dirty = dirty
}

/** flowgate.v02.0074 T#2 — the plan's window holds unsaved edits, or its Step windows edits not applied yet. */
export function workPlanEditUnsaved(docId: string): boolean {
  return workPlanEditWindowOf(docId)?.dirty === true || workPlanStepEditsPending(docId)
}

/**
 * flowgate.v02.0074 T#2 — registers the opener of windows asked for from a document line or window (the Inspector: its
 * Flow's verdict and the Canvas read after a save, as openWorkPlan); null removes it. Returns the function that removes
 * this one.
 */
export function setWorkPlanEditOpener(opener: WorkPlanEditOpener | null): () => void {
  editOpener.value = opener
  return () => {
    if (editOpener.value === opener) editOpener.value = null
  }
}

/** The registered opener's part of a request (reactive); without one the window is run-locked and tells no one. */
export function workPlanEditOpenerOf(docId: string, flowId: string): Pick<WorkPlanEditRequest, 'external' | 'saved'> {
  const opener = editOpener.value
  return opener ? opener(docId, flowId) : { external: () => 'run_locked' }
}

/**
 * flowgate.v02.0074 T#2 (D#1 §6.3) — opens the plan's window asking for [JSON 가져오기]. An open window is reused (its
 * external · saved · title kept, brought back and to the front); else one is opened with the registered opener's part.
 */
export function openWorkPlanEditImport(docId: string, context: { projectId: string | null; groupId: string | null }): void {
  if (!docId) return
  const open = workPlanEditWindowOf(docId)
  if (open) {
    openWorkPlanEditWindow({ ...open.request, initialMode: 'import' })
    return
  }
  const groupId = context.groupId ?? workPlanGroupOf(docId)
  openWorkPlanEditWindow({ docId, projectId: context.projectId, groupId, initialMode: 'import', ...workPlanEditOpenerOf(docId, groupId ?? '') })
}

/** Removes the window (the window asked about unsaved edits first); its Step windows are told. */
export function closeWorkPlanEditWindow(docId: string): void {
  const index = windows.findIndex((win) => win.docId === docId)
  if (index === -1) return
  windows.splice(index, 1)
  stepHandler.value?.parentClosed?.(docId)
}

/** Closes every edit window (sign-out, tests). */
export function closeAllWorkPlanEditWindows(): void {
  for (const win of [...windows]) closeWorkPlanEditWindow(win.docId)
  cascade = 0
}

/** Registers the Step window feature (T#2); null removes it. Returns the function that removes this one. */
export function setWorkPlanStepEditHandler(handler: WorkPlanStepEditHandler | null): () => void {
  stepHandler.value = handler
  return () => {
    if (stepHandler.value === handler) stepHandler.value = null
  }
}

/** A Step window can be opened (reactive): ✎ is enabled. */
export function workPlanStepEditAvailable(): boolean {
  return stepHandler.value !== null
}

/** Hands a row to the Step window; false when none is registered. */
export function requestWorkPlanStepEdit(target: WorkPlanStepEditTarget, session: WorkPlanStepEditSession): boolean {
  const handler = stepHandler.value
  if (!handler) return false
  handler.open(target, session)
  return true
}

/** Step windows of the document hold edits not applied to the parent yet. */
export function workPlanStepEditsPending(docId: string): boolean {
  return stepHandler.value?.pending?.(docId) === true
}

/** A Step window of the row is open (reactive). */
export function workPlanStepEditOpen(docId: string, rowId: string): boolean {
  return stepHandler.value?.openRow?.(docId, rowId) === true
}

/** Explicit reload discards child drafts after the parent's confirmation. */
export function discardWorkPlanStepEdits(docId: string): void {
  stepHandler.value?.parentClosed?.(docId)
}
