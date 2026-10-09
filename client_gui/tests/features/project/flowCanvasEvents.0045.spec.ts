/**
 * flowgate.v02.0045 T0011 (S2) — Canvas change signals over the existing SSE stream (P0008 §8.1 /
 * §8.3): project-scoped stream with the current token, `graph_canvas_changed` of another tab /
 * device / user reported per Flow, this tab's own events (origin_client_id) dropped, a resync
 * after a reconnect, a fresh stream after a closed one, and unsubscribe closing the stream.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  putRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID } from '@/features/project/flowCanvasApi'
import { CANVAS_STREAM_RETRY_MS, changedFlowOf, subscribeFlowCanvasChanges } from '@/features/project/flowCanvasEvents'

class FakeEventSource {
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

  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  emit(type: string, data?: unknown) {
    if (type === 'open') this.readyState = FakeEventSource.OPEN
    const event = Object.assign(new Event(type), { data: data === undefined ? undefined : JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

const frame = (flowId: string, origin: string | null, type = 'graph_canvas_changed') => ({
  event_type: type,
  payload: { flow_id: flowId, reason: 'layout', actor_user_id: 'u_kim', origin_client_id: origin, node_keys: [] },
  project: 'flowgate',
  group_id: flowId,
  doc_id: null,
})

describe('Canvas change subscription', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('fg_access_token', 'jwt-1')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    sessionStorage.clear()
  })

  it("opens the existing project stream and reports other tabs' changes per Flow, not its own", () => {
    const changed: string[] = []
    const resync = vi.fn()
    const stop = subscribeFlowCanvasChanges('flowgate', { changed: (f) => changed.push(f), resync })
    const es = FakeEventSource.all[0]
    const url = new URL(es.url)
    expect(url.pathname.endsWith('/api/v1/events/stream')).toBe(true)
    expect([url.searchParams.get('project'), url.searchParams.get('token')]).toEqual(['flowgate', 'jwt-1'])
    es.emit('open')
    es.emit('graph_canvas_changed', frame('f.0051', 'tab_other'))
    es.emit('graph_canvas_changed', frame('f.0051', CANVAS_CLIENT_ID))
    es.emit('graph_canvas_changed', frame('f.0048', null))
    es.emit('flow_ownership_changed', frame('f.0049', 'tab_other', 'flow_ownership_changed'))
    expect(changed).toEqual(['f.0051', 'f.0048', 'f.0049'])
    expect(resync).not.toHaveBeenCalled()
    stop()
    expect(es.closed).toBe(true)
  })

  it('resyncs after a reconnect and opens a fresh stream (new token) after a closed one', () => {
    const resync = vi.fn()
    const stop = subscribeFlowCanvasChanges('flowgate', { changed: () => {}, resync })
    const first = FakeEventSource.all[0]
    first.emit('open')
    first.readyState = FakeEventSource.CONNECTING
    first.emit('error')
    first.emit('open')
    expect(resync).toHaveBeenCalledTimes(1)
    expect(FakeEventSource.all).toHaveLength(1)
    sessionStorage.setItem('fg_access_token', 'jwt-2')
    first.readyState = FakeEventSource.CLOSED
    first.emit('error')
    vi.advanceTimersByTime(CANVAS_STREAM_RETRY_MS)
    const second = FakeEventSource.all[1]
    expect(new URL(second.url).searchParams.get('token')).toBe('jwt-2')
    second.emit('open')
    expect(resync).toHaveBeenCalledTimes(2)
    stop()
    expect(second.closed).toBe(true)
  })

  it('stops a pending reopen on unsubscribe, ignores unreadable frames and is a no-op without EventSource', () => {
    const stop = subscribeFlowCanvasChanges('flowgate', { changed: () => {}, resync: () => {} })
    FakeEventSource.all[0].readyState = FakeEventSource.CLOSED
    FakeEventSource.all[0].emit('error')
    stop()
    vi.advanceTimersByTime(CANVAS_STREAM_RETRY_MS * 2)
    expect(FakeEventSource.all).toHaveLength(1)
    expect(changedFlowOf('not json')).toBeNull()
    expect(changedFlowOf(JSON.stringify({ payload: {} }))).toBeNull()
    vi.unstubAllGlobals()
    vi.stubGlobal('EventSource', undefined)
    expect(() => subscribeFlowCanvasChanges('flowgate', { changed: () => {}, resync: () => {} })()).not.toThrow()
  })
})
