// flowgate.v02test.0013 — the open chat hears the server's events. Until now the part had no push channel
// (conversationSource.ts): a chat was brought up to date only by the poll of a run it tracks, so a run started
// elsewhere, a run missed when the chat opened, or a message / command / run change written while no run was
// polled stayed off screen until the panel was visited again. The container hands in the project event stream
// (`subscribeEvents`, the project hub's subscribe; this part knows no store) and every frame about the open chat
// asks the session to recover what it may have changed (session.recover). Events are signals, never state: the
// session reads the server again, merging turns by seq and replacing activities by id, so a frame heard twice or
// late shows nothing twice. A reconnect of the stream (frames may be missing) asks for everything.
import { onScopeDispose, watch } from 'vue'
import { FULL_CHAT_REFRESH, mergeChatRefresh, type ChatRefresh } from './conversationSession'

/** Frames within this window of the first are one recovery. */
export const CHAT_EVENT_COALESCE_MS = 200

/** One server event (the project hub's frame: SSE event name, payload and routing fields). */
export interface ChatEventFrame {
  type: string
  payload: Record<string, unknown>
  groupId: string | null
  docId: string | null
}

export interface ChatEventSubscription {
  events: readonly string[]
  event(frame: ChatEventFrame): void
  /** The stream reconnected after a drop. */
  resync?(): void
}

/** Subscribes to a project's events; returns the unsubscribe. */
export type SubscribeChatEvents = (projectId: string, subscription: ChatEventSubscription) => () => void

/**
 * What each server event may have changed in its chat. The CH is the frame's doc_id for a turn and for an AI run
 * (the run's doc_ref); command requests and run changes travel with a null doc_id and the CH in `payload.doc_id`.
 * - conversation_turn_appended: a message was stored (by anyone, from anywhere).
 * - ai_invoke_started: a run of the chat began — it is adopted and polled.
 * - ai_invoke_finished: it ended — its reply, changes and terminal state (the poll's own end when it is polled).
 * - chat_command_updated / chat_run_changes: a command request changed state / a run's changed files were stored.
 */
export const CHAT_EVENT_REFRESH: Readonly<Record<string, ChatRefresh>> = Object.freeze({
  conversation_turn_appended: { turns: true },
  ai_invoke_started: { run: true },
  ai_invoke_finished: { turns: true, activities: true, history: true },
  chat_command_updated: { activities: true },
  chat_run_changes: { activities: true },
})

export const CHAT_REFRESH_EVENTS: readonly string[] = Object.freeze(Object.keys(CHAT_EVENT_REFRESH))

/** The open chat a frame is matched against. */
export interface ChatEventTarget {
  projectId: string
  groupId: string
  chatId: string
}

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)

/** What the frame asks the open chat to read again, or null when it is not about that chat. */
export function chatEventRefresh(frame: ChatEventFrame, target: ChatEventTarget): ChatRefresh | null {
  const parts = Object.prototype.hasOwnProperty.call(CHAT_EVENT_REFRESH, frame.type) ? CHAT_EVENT_REFRESH[frame.type] : null
  if (!parts) return null
  const groupId = frame.groupId ?? text(frame.payload.group_id)
  if (groupId !== null && groupId !== target.groupId) return null
  const docId = frame.docId ?? text(frame.payload.doc_id) ?? text(frame.payload.doc_ref)
  return docId === target.chatId ? parts : null
}

const targetKey = (target: ChatEventTarget | null) => (target ? `${target.projectId}\n${target.groupId}\n${target.chatId}` : '')

/**
 * Calls `refresh` with what the open chat should read again: the union of the matching frames heard within
 * CHAT_EVENT_COALESCE_MS, or everything after a reconnect. One subscription per project, kept across chat and Flow
 * switches of that project; the target is read when a frame arrives, and a pending request belongs to the target
 * it was raised for. Must run in a component setup or an effect scope; stops with it. No subscriber: nothing.
 */
export function useChatEvents(
  subscribe: SubscribeChatEvents | null | undefined,
  target: () => ChatEventTarget | null,
  refresh: (parts: ChatRefresh) => void,
): void {
  let pending: { timer: ReturnType<typeof setTimeout>; parts: ChatRefresh } | null = null

  const cancel = () => {
    if (pending !== null) clearTimeout(pending.timer)
    pending = null
  }
  const signal = (parts: ChatRefresh) => {
    if (pending !== null) {
      pending.parts = mergeChatRefresh(pending.parts, parts)
      return
    }
    const entry = {
      parts: mergeChatRefresh(null, parts),
      timer: setTimeout(() => {
        if (pending !== entry) return
        pending = null
        refresh(entry.parts)
      }, CHAT_EVENT_COALESCE_MS),
    }
    pending = entry
  }

  watch(
    () => target()?.projectId ?? null,
    (projectId, _previous, onCleanup) => {
      if (!subscribe || projectId === null) return
      onCleanup(
        subscribe(projectId, {
          events: CHAT_REFRESH_EVENTS,
          event: (frame) => {
            const current = target()
            const parts = current && current.projectId === projectId ? chatEventRefresh(frame, current) : null
            if (parts) signal(parts)
          },
          resync: () => {
            if (target() !== null) signal(FULL_CHAT_REFRESH)
          },
        }),
      )
    },
    { immediate: true },
  )
  watch(() => targetKey(target()), cancel)
  onScopeDispose(cancel)
}
