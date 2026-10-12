// flowgate.v02.0053 T#3 (rework) — an open document window hears its document's server events on the
// v0.2 project event hub (features/project projectEvents.ts, through the project store), so the window
// shows what the server has now: the AI filling a report that opened empty, an edit or a review
// elsewhere. The v0.1 `fg:document_content_changed` window event is sent by the v0.1 screen's SSE
// composable only, which the v0.2 App does not run.
// - A frame counts when it is about one of the window's documents (the one it was opened for and the
//   ones its supersede chain reached: the frame's doc_id, the payload's, or the documents a finished AI
//   run reached). ai_invoke_finished goes to every viewer of the project; the inbox edit's
//   document_explorer_refresh goes to the submitting user, so the run's end is what tells another
//   viewer that the AI filled the report.
// - A run result is a new document (rework 2): the finished run names it, not the report the window
//   opened, and the run's group_view_refresh of the Flow is what asks the window to read the report's
//   supersede link again (the title line read follows the chain).
// - Content events ask for the body again at once (the MD Viewer coalesces them itself); every listed
//   event asks for the title line again, coalesced within DOCUMENT_WINDOW_REFRESH_COALESCE_MS.
// - A reconnect (events may be missing) asks for both.
// - Not every write sends an event (rework 2): the v0.1 content save (PATCH /documents/content, any tab)
//   only updates the row. While the window is open it asks for both every DOCUMENT_WINDOW_POLL_MS, except
//   while it is minimized or the tab is hidden.
// - Events are signals only: the caller reads the server again. Leaving (window unmount) unsubscribes
//   and stops the polling.
import { getCurrentInstance, onScopeDispose, watch } from 'vue'
import { useProjectStore, type ProjectEventFrame, type ProjectEventSubscription } from '@/features/project'

/** Server events (event_type) after which an open document window reads its document again. */
export const DOCUMENT_WINDOW_EVENTS = [
  'document_explorer_refresh',
  'doc_review_status_changed',
  'ai_review_arrived',
  'ai_invoke_finished',
  'group_view_refresh',
] as const

/** The events among them that may have changed the stored body. */
const CONTENT_EVENTS: ReadonlySet<string> = new Set(['document_explorer_refresh', 'ai_invoke_finished'])

/** Window after a first matching event within which further events join the same title line read. */
export const DOCUMENT_WINDOW_REFRESH_COALESCE_MS = 200

/** Period of the open window's own reads (writes that send no event: the v0.1 content save). */
export const DOCUMENT_WINDOW_POLL_MS = 5000

/** A body change of the document (what the MD Viewer reads again for). */
export interface DocumentContentChange {
  readonly docId: string
  readonly revisionNo: number | null
}

/** What the window stands for, read on every frame. */
export interface DocumentWindowEventTarget {
  /** The document opened and the documents its supersede chain reached. */
  docIds(): readonly string[]
  /** The document shown (whose body is read again). */
  shownId(): string
  /** The document's Flow (v0.1 group id), once it is read. */
  groupId(): string | null
  /** The document's project, once it is read; a change re-subscribes. */
  projectId(): string | null
  /** The polling skips its reads while this holds (a minimized window). */
  paused(): boolean
}

export interface DocumentWindowEventHandlers {
  /** The stored body may have changed. */
  content(change: DocumentContentChange): void
  /** The title / state, or the supersede chain, may have changed. */
  record(): void
}

/**
 * Whether a frame is about one of the documents: its doc_id or the payload's, or a finished AI run that
 * reached it (`reached_doc_ids` — a run on a task names the report it wrote there; its doc_id is the task).
 */
export function documentWindowEventMatches(frame: ProjectEventFrame, docIds: string | readonly string[]): boolean {
  const ids = typeof docIds === 'string' ? [docIds] : docIds
  const payloadDocId = frame.payload.doc_id
  if (ids.some((id) => frame.docId === id || payloadDocId === id)) return true
  const reached = frame.payload.reached_doc_ids
  return Array.isArray(reached) && ids.some((id) => reached.includes(id))
}

/** Whether a frame is a refresh of the document's whole Flow (group_view_refresh of its group). */
export function documentWindowFlowEventMatches(frame: ProjectEventFrame, groupId: string | null): boolean {
  if (frame.type !== 'group_view_refresh' || groupId === null) return false
  return (frame.groupId ?? frame.payload.group_id) === groupId
}

type SubscribeProjectEvents = (projectId: string, subscription: ProjectEventSubscription) => () => void

// The store lives in the app's Pinia. A window mounted without one (a unit test with no app plugins)
// has no project stream to hear and subscribes to nothing.
function projectEventSubscriber(): SubscribeProjectEvents | null {
  const pinia = getCurrentInstance()?.appContext.config.globalProperties.$pinia
  if (!pinia) return null
  const store = useProjectStore(pinia)
  return (projectId, subscription) => store.subscribeProjectEvents(projectId, subscription)
}

function tabHidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

/**
 * Subscribes the document's window to its project's events and polls while it is open. The target is
 * read reactively (the project is known once the document is read). Must run in a component setup.
 */
export function useDocumentWindowEvents(target: DocumentWindowEventTarget, handlers: DocumentWindowEventHandlers): void {
  const subscribe = projectEventSubscriber()
  let pending: ReturnType<typeof setTimeout> | null = null

  const cancel = () => {
    if (pending !== null) clearTimeout(pending)
    pending = null
  }
  const recordChanged = () => {
    if (pending !== null) return
    pending = setTimeout(() => {
      pending = null
      handlers.record()
    }, DOCUMENT_WINDOW_REFRESH_COALESCE_MS)
  }
  const readBoth = () => {
    handlers.content({ docId: target.shownId(), revisionNo: null })
    recordChanged()
  }

  watch(
    () => target.projectId(),
    (project, _previous, onCleanup) => {
      if (subscribe === null || project === null) return
      onCleanup(
        subscribe(project, {
          events: DOCUMENT_WINDOW_EVENTS,
          event: (frame) => {
            if (documentWindowFlowEventMatches(frame, target.groupId())) {
              recordChanged()
              return
            }
            if (!documentWindowEventMatches(frame, target.docIds())) return
            if (CONTENT_EVENTS.has(frame.type)) {
              const revision = frame.payload.revision_no
              const shown = target.shownId()
              // A revision number belongs to the document the frame names; the shown one may be another.
              const sameDoc = frame.docId === shown || frame.payload.doc_id === shown
              handlers.content({ docId: shown, revisionNo: sameDoc && typeof revision === 'number' ? revision : null })
            }
            recordChanged()
          },
          resync: readBoth,
        }),
      )
    },
    { immediate: true },
  )

  const poll = setInterval(() => {
    if (target.paused() || tabHidden()) return
    readBoth()
  }, DOCUMENT_WINDOW_POLL_MS)

  onScopeDispose(() => {
    cancel()
    clearInterval(poll)
  })
}
