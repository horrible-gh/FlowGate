// flowgate.v02.0056 F2-2 — the AI call windows of documents (O3 0009-TR §4-3) and the runs they started. One window
// per document, kept like the document windows (module state of this tab, opening it again brings it back). The
// window calls the AI only when [실행] is pressed (DocumentAiWindow.vue → documentAiStart).
//
// A run started here is followed until it ends: its state is read (GET /ai-invoke/{run_id}) every
// DOCUMENT_AI_POLL_MS, and the group's live run (GET /ai-invoke/active?group_id=) when the window opens and after
// the run ends. The document lines show it (documentAiRunning: [AI 호출] says the AI works); the opener hears the
// end (`finished`, the Canvas is read again). A run that is gone (404 / 410) ends; any other failed read keeps
// following.
//
// A live run the group read finds that this tab did not start (another tab or user) is followed the same way
// until it ends — its state read every DOCUMENT_AI_POLL_MS — and then the group's live run is read again, so the
// line and the window's note do not stay on "AI 작업 중" after it is over; the window open on its document hears
// the end too.
import { reactive } from 'vue'
import { documentAiActiveRead, documentAiRunRead, type DocumentAiActivity, type DocumentAiRunState } from '@/features/document'

/** What the AI call window works on and whom it tells. */
export interface DocumentAiRequest {
  readonly docId: string
  /** The Flow = v0.1 group id of the document. */
  readonly groupId: string
  /** The project the AI choices belong to. */
  readonly projectId: string
  /** The line's name (지시서 · 레포트 …) and the document's title, for the window's title line. */
  readonly name: string
  /** flowgate.v02.0088 F15: the i18n key of `name` (UI only, as SlotWriteRequest.nameKey; windows stay keyed by docId). */
  readonly nameKey?: string | null
  readonly title: string
  /** Told when a run started here ends (the opener reads the Canvas again). */
  readonly finished?: () => void
  /** Recheck the opener's guard and lock before the start request is sent. */
  readonly submitting?: () => boolean
  readonly rejected?: () => void
}

export interface DocumentAiWindowState {
  readonly docId: string
  request: DocumentAiRequest
  minimized: boolean
  readonly x: number
  readonly y: number
  raise: number
}

/** A run started from a window, as the lines and the window follow it. */
export interface DocumentAiTrackedRun extends DocumentAiRunState {
  readonly docId: string
  readonly groupId: string
}

/** How often a started run's state is read. */
export const DOCUMENT_AI_POLL_MS = 3000

const CASCADE_STEPS = 6
const CASCADE_PX = 28
const ORIGIN_X = 300
const ORIGIN_Y = 120

const windows = reactive<DocumentAiWindowState[]>([])
const runs = reactive(new Map<string, DocumentAiTrackedRun>())
const activities = reactive(new Map<string, DocumentAiActivity>())
const timers = new Map<string, ReturnType<typeof setTimeout>>()
const listeners = new Map<string, () => void>()
/** Per group: the live run started elsewhere that is followed until it ends (its poll timer below). */
const watched = new Map<string, string>()
const watchTimers = new Map<string, ReturnType<typeof setTimeout>>()
let cascade = 0

export function documentAiWindowList(): readonly DocumentAiWindowState[] {
  return windows
}

export function documentAiWindowOf(docId: string): DocumentAiWindowState | undefined {
  return windows.find((win) => win.docId === docId)
}

/** Opens a document's AI call window (nothing is called yet); one already open is brought back. */
export function openDocumentAiWindow(request: DocumentAiRequest): void {
  if (!request.docId) return
  const open = documentAiWindowOf(request.docId)
  if (open) {
    open.request = request
    open.minimized = false
    open.raise += 1
  } else {
    const offset = (cascade++ % CASCADE_STEPS) * CASCADE_PX
    windows.push({ docId: request.docId, request, minimized: false, x: ORIGIN_X + offset, y: ORIGIN_Y + offset, raise: 0 })
  }
  void readDocumentAiActivity(request.groupId)
}

export function setDocumentAiWindowMinimized(docId: string, minimized: boolean): void {
  const open = documentAiWindowOf(docId)
  if (open) open.minimized = minimized
}

export function closeDocumentAiWindow(docId: string): void {
  const index = windows.findIndex((win) => win.docId === docId)
  if (index !== -1) windows.splice(index, 1)
}

// --- Runs ----------------------------------------------------------------------------------------------

/** The run started here for a document (the last one), or undefined. */
export function documentAiRunOf(docId: string): DocumentAiTrackedRun | undefined {
  return runs.get(docId)
}

/** The group's live run as last read, or undefined. */
export function documentAiActivityOf(groupId: string): DocumentAiActivity | undefined {
  return activities.get(groupId)
}

/** An AI run works on the document now: one started here that has not ended, or the group's live run on it. */
export function documentAiRunning(docId: string): boolean {
  const run = runs.get(docId)
  if (run && !run.finished) return true
  for (const activity of activities.values()) if (activity.active && activity.docRef === docId) return true
  return false
}

const statusOf = (error: unknown): number | null => {
  const status = (error as { response?: { status?: unknown } } | null)?.response?.status
  return typeof status === 'number' ? status : null
}

/**
 * Reads the group's live run (kept for the lines and the window); a failed read keeps the last one. A live run
 * not started here is followed until it ends.
 */
export async function readDocumentAiActivity(groupId: string): Promise<DocumentAiActivity | null> {
  try {
    const activity = await documentAiActiveRead(groupId)
    activities.set(groupId, activity)
    watchActivity(groupId, activity)
    return activity
  } catch {
    return null
  }
}

/** A run started here that is still followed. */
function followedHere(runId: string): boolean {
  for (const run of runs.values()) if (run.runId === runId && !run.finished) return true
  return false
}

function unwatch(groupId: string): void {
  clearTimeout(watchTimers.get(groupId))
  watchTimers.delete(groupId)
  watched.delete(groupId)
}

function scheduleWatch(groupId: string): void {
  clearTimeout(watchTimers.get(groupId))
  watchTimers.set(
    groupId,
    setTimeout(() => void watchPoll(groupId), DOCUMENT_AI_POLL_MS),
  )
}

/** Follows the group's live run when this tab did not start it (a run started here is followed by poll). */
function watchActivity(groupId: string, activity: DocumentAiActivity): void {
  if (!activity.active || !activity.runId || followedHere(activity.runId)) {
    unwatch(groupId)
    return
  }
  // Already followed: a poll is waiting or reading.
  if (watched.get(groupId) === activity.runId) return
  watched.set(groupId, activity.runId)
  scheduleWatch(groupId)
}

async function watchPoll(groupId: string): Promise<void> {
  const runId = watched.get(groupId)
  if (!runId) return
  let over: boolean
  try {
    over = (await documentAiRunRead(runId)).finished
  } catch (error) {
    const status = statusOf(error)
    over = status === 404 || status === 410
  }
  // The group was read again meanwhile and follows another run (or none).
  if (watched.get(groupId) !== runId) return
  if (!over) {
    scheduleWatch(groupId)
    return
  }
  unwatch(groupId)
  const last = activities.get(groupId)
  if (last?.runId === runId) activities.set(groupId, { ...last, active: false, status: 'finished' })
  if (last?.docRef) documentAiWindowOf(last.docRef)?.request.finished?.()
  void readDocumentAiActivity(groupId)
}

function schedule(docId: string): void {
  clearTimeout(timers.get(docId))
  timers.set(
    docId,
    setTimeout(() => void poll(docId), DOCUMENT_AI_POLL_MS),
  )
}

function end(docId: string, run: DocumentAiTrackedRun): void {
  runs.set(docId, run)
  timers.delete(docId)
  const finished = listeners.get(docId)
  listeners.delete(docId)
  void readDocumentAiActivity(run.groupId)
  finished?.()
}

async function poll(docId: string): Promise<void> {
  const run = runs.get(docId)
  if (!run || run.finished) return
  try {
    const state = await documentAiRunRead(run.runId)
    // Another run of the document may have been started meanwhile: only the run still followed is updated.
    if (runs.get(docId)?.runId !== run.runId) return
    const next: DocumentAiTrackedRun = { ...state, docId, groupId: run.groupId }
    if (next.finished) end(docId, next)
    else {
      runs.set(docId, next)
      schedule(docId)
    }
  } catch (error) {
    if (runs.get(docId)?.runId !== run.runId) return
    const status = statusOf(error)
    if (status === 404 || status === 410) end(docId, { ...run, finished: true, endReason: run.endReason ?? 'gone' })
    else schedule(docId)
  }
}

/** Follows a run started for a document until it ends; `finished` is told then. */
export function trackDocumentAiRun(docId: string, groupId: string, run: DocumentAiRunState, finished?: () => void): void {
  const tracked: DocumentAiTrackedRun = { ...run, docId, groupId }
  if (finished) listeners.set(docId, finished)
  else listeners.delete(docId)
  if (tracked.finished) {
    end(docId, tracked)
    return
  }
  runs.set(docId, tracked)
  schedule(docId)
}

/** Closes every AI call window and stops following every run (sign-out, tests). */
export function closeAllDocumentAiWindows(): void {
  windows.splice(0, windows.length)
  for (const timer of timers.values()) clearTimeout(timer)
  timers.clear()
  for (const timer of watchTimers.values()) clearTimeout(timer)
  watchTimers.clear()
  watched.clear()
  listeners.clear()
  runs.clear()
  activities.clear()
  cascade = 0
}
