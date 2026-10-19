// flowgate.v02.0072 T#1 — the work plan edit windows of this tab, kept the way the document windows are
// (documentWindows.ts / slotWriteWindows.ts): module state, one window per work plan document, opening it again brings
// it back (and, when it was minimized, restores it). flowgate.v02.0072 T#3: the WP card's 「작업계획」 line opens it (the
// Inspector's openDocument.ts openWorkPlan); the App Shell mounts WorkPlanEditWindows once (through DocumentWindows).
//
// The Step window boundary (T#2). A Step row's ✎ does not open anything inside this window (no inline drawer): the
// window hands the row to the Step window's handler, registered here by the Step window feature
// (setWorkPlanStepEditHandler). The handler gets the row's target and a session bound to the parent window's editor
// (useWorkPlanEditor.stepSession): read a copy of the row's review / pre-instruction values, put edited values back on
// that row only, and ask whether the parent is locked or still open. The final server save stays the parent's [저장].
// While no handler is registered, ✎ is shown disabled with its reason.
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
}

export interface WorkPlanEditWindowState {
  readonly docId: string
  request: WorkPlanEditRequest
  minimized: boolean
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
}

const CASCADE_STEPS = 6
const CASCADE_PX = 28
const ORIGIN_X = 200
const ORIGIN_Y = 88

const windows = reactive<WorkPlanEditWindowState[]>([])
let cascade = 0
const stepHandler = shallowRef<WorkPlanStepEditHandler | null>(null)

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
  windows.push({ docId: request.docId, request, minimized: false, x: ORIGIN_X + offset, y: ORIGIN_Y + offset, raise: 0 })
}

export function setWorkPlanEditWindowMinimized(docId: string, minimized: boolean): void {
  const open = workPlanEditWindowOf(docId)
  if (open) open.minimized = minimized
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

/** Explicit reload discards child drafts after the parent's confirmation. */
export function discardWorkPlanStepEdits(docId: string): void {
  stepHandler.value?.parentClosed?.(docId)
}
