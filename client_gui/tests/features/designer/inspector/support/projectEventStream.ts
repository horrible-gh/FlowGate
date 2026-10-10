/**
 * flowgate.v02.0048 T#3 — the event hub mock of the shared Inspector test support (D#1 §12.3).
 * It replaces the browser EventSource, so the real project event hub (features/project
 * projectEvents.ts) runs under the test: subscriptions open real hub connections, and the test
 * pushes server frames into them, drops / reopens the stream and counts the connections.
 *
 *   const stream = installProjectEventStream()   // in beforeEach
 *   stream.open()                                // the server accepted the stream
 *   stream.emit('doc_review_status_changed', { docId: 'flowgate.v02.0048.0011-TR' })
 *   stream.reconnect()                           // a dropped stream opened again: subscribers resync
 *   removeProjectEventStream()                   // in afterEach, after every subscriber is unmounted
 */
import { vi } from 'vitest'

export class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static all: FakeEventSource[] = []
  readyState = FakeEventSource.CONNECTING
  closed = false
  private listeners = new Map<string, Array<(event: Event) => void>>()

  constructor(readonly url: string) {
    FakeEventSource.all.push(this)
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  /** Listeners attached for one event name. */
  listened(type: string): number {
    return this.listeners.get(type)?.length ?? 0
  }

  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  dispatch(type: string, data?: string) {
    if (type === 'open') this.readyState = FakeEventSource.OPEN
    const event = Object.assign(new Event(type), { data })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

/** Routing fields of a server frame (the SSE data: event_type, payload, project, group_id, doc_id). */
export interface ServerFrameFields {
  payload?: Record<string, unknown>
  groupId?: string | null
  docId?: string | null
  project?: string
}

export interface ProjectEventStream {
  /** The server accepted the open connections (their first open: no resync). */
  open(): void
  /** Connections not closed yet. */
  connections(): FakeEventSource[]
  /** One server event on every open connection. */
  emit(type: string, fields?: ServerFrameFields): void
  /** A dropped stream opened again (the hub tells every subscriber to resync). */
  reconnect(): void
}

export function installProjectEventStream(): ProjectEventStream {
  FakeEventSource.all = []
  vi.stubGlobal('EventSource', FakeEventSource)
  const connections = () => FakeEventSource.all.filter((es) => !es.closed)
  return {
    connections,
    open: () => connections().forEach((es) => es.dispatch('open')),
    emit: (type, fields = {}) => {
      const data = JSON.stringify({
        event_type: type,
        payload: fields.payload ?? {},
        timestamp: '2026-10-09T15:00:00+09:00',
        project: fields.project ?? 'flowgate',
        group_id: fields.groupId ?? null,
        doc_id: fields.docId ?? null,
      })
      connections().forEach((es) => es.dispatch(type, data))
    },
    reconnect: () => connections().forEach((es) => es.dispatch('open')),
  }
}

export function removeProjectEventStream() {
  vi.unstubAllGlobals()
  FakeEventSource.all = []
}
