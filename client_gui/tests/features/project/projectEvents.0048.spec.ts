/**
 * flowgate.v02.0048 T#2 — the project event hub (D#1 §7.1): one EventSource per project shared by
 * every subscriber (the Canvas adapter and any other), closed with the last unsubscribe, the same
 * reconnect as the Canvas stream had (resync after a drop, a fresh stream with the current token
 * after a closed one), and frames passed on as signals (name, payload, group_id, doc_id) only.
 * The Canvas adapter's own behaviour stays covered, unchanged, by flowCanvasEvents.0045.spec.ts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  putRequest: vi.fn(),
  postRequest: vi.fn(),
  deleteRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID } from '@/features/project/flowCanvasApi'
import { CANVAS_STREAM_RETRY_MS, subscribeFlowCanvasChanges } from '@/features/project/flowCanvasEvents'
import {
  PROJECT_STREAM_RETRY_MS,
  parseProjectEvent,
  subscribeProjectEvents,
  type ProjectEventFrame,
} from '@/features/project/projectEvents'
import { useProjectStore } from '@/features/project/store'

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

  listened(type: string): number {
    return this.listeners.get(type)?.length ?? 0
  }

  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  emit(type: string, data?: unknown) {
    if (type === 'open') this.readyState = FakeEventSource.OPEN
    const event = Object.assign(new Event(type), {
      data: data === undefined ? undefined : typeof data === 'string' ? data : JSON.stringify(data),
    })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

const open = () => FakeEventSource.all.filter((es) => !es.closed)

const frame = (type: string, payload: Record<string, unknown>, groupId: string | null = null, docId: string | null = null) => ({
  event_type: type,
  payload,
  timestamp: '2026-10-09T15:00:00+09:00',
  project: 'flowgate',
  group_id: groupId,
  doc_id: docId,
})

function recorder(events: readonly string[]) {
  const frames: ProjectEventFrame[] = []
  const resync = vi.fn()
  return { frames, resync, subscription: { events, event: (f: ProjectEventFrame) => frames.push(f), resync } }
}

// Every subscription a case makes is released after it, so a failing case leaves no hub behind.
const stops: Array<() => void> = []
function hub(projectId: string, subscription: Parameters<typeof subscribeProjectEvents>[1]) {
  const stop = subscribeProjectEvents(projectId, subscription)
  stops.push(stop)
  return stop
}
function canvas(projectId: string, handlers: Parameters<typeof subscribeFlowCanvasChanges>[1]) {
  const stop = subscribeFlowCanvasChanges(projectId, handlers)
  stops.push(stop)
  return stop
}

describe('project event hub', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('fg_access_token', 'jwt-1')
  })
  afterEach(() => {
    for (const stop of stops.splice(0)) stop()
    vi.unstubAllGlobals()
    vi.useRealTimers()
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  it('keeps the Canvas retry wait the hub uses', () => {
    expect(CANVAS_STREAM_RETRY_MS).toBe(PROJECT_STREAM_RETRY_MS)
  })

  it('two subscribers (Canvas + another) share one connection and each hears only its names', () => {
    const changed: string[] = []
    const stopCanvas = canvas('flowgate', { changed: (f) => changed.push(f), resync: () => {} })
    const other = recorder(['doc_review_status_changed'])
    const stopOther = hub('flowgate', other.subscription)

    expect(FakeEventSource.all).toHaveLength(1)
    const es = FakeEventSource.all[0]
    const url = new URL(es.url)
    expect(url.pathname.endsWith('/api/v1/events/stream')).toBe(true)
    expect([url.searchParams.get('project'), url.searchParams.get('token')]).toEqual(['flowgate', 'jwt-1'])
    // One listener per name on the shared connection, added when a subscriber asks for it.
    expect(['graph_canvas_changed', 'flow_ownership_changed', 'doc_review_status_changed'].map((t) => es.listened(t))).toEqual([1, 1, 1])

    es.emit('open')
    es.emit('graph_canvas_changed', frame('graph_canvas_changed', { flow_id: 'f.0051', origin_client_id: 'tab_other' }, 'f.0051'))
    es.emit('graph_canvas_changed', frame('graph_canvas_changed', { flow_id: 'f.0051', origin_client_id: CANVAS_CLIENT_ID }, 'f.0051'))
    es.emit('doc_review_status_changed', frame('doc_review_status_changed', { status: 'approved' }, 'f.0048', 'd.0011-TR'))

    expect(changed).toEqual(['f.0051'])
    expect(other.frames).toEqual([
      { type: 'doc_review_status_changed', payload: { status: 'approved' }, groupId: 'f.0048', docId: 'd.0011-TR' },
    ])
    stopCanvas()
    stopOther()
  })

  it('a later subscriber on an open connection adds only its missing names and hears no earlier event', () => {
    const first = recorder(['qna_q_registered'])
    const stopFirst = hub('flowgate', first.subscription)
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('qna_q_registered', frame('qna_q_registered', {}, 'f.0048', 'd.1'))

    const second = recorder(['qna_q_registered', 'test_run_finished'])
    const stopSecond = hub('flowgate', second.subscription)
    expect(FakeEventSource.all).toHaveLength(1)
    expect([es.listened('qna_q_registered'), es.listened('test_run_finished')]).toEqual([1, 1])
    // The hub keeps no frame: the second subscriber does not get the event it was not there for.
    expect(second.frames).toEqual([])

    es.emit('qna_q_registered', frame('qna_q_registered', {}, 'f.0048', 'd.2'))
    es.emit('test_run_finished', frame('test_run_finished', { run_id: 'r1' }, null, 'd.3'))
    expect(first.frames.map((f) => f.docId)).toEqual(['d.1', 'd.2'])
    expect(second.frames.map((f) => [f.type, f.docId])).toEqual([
      ['qna_q_registered', 'd.2'],
      ['test_run_finished', 'd.3'],
    ])
    stopFirst()
    stopSecond()
  })

  it('closes the connection only when the last subscriber leaves; a new subscription opens a new one', () => {
    const stopCanvas = canvas('flowgate', { changed: () => {}, resync: () => {} })
    const stopOther = hub('flowgate', recorder(['ai_invoke_finished']).subscription)
    const es = FakeEventSource.all[0]

    stopCanvas()
    expect(es.closed).toBe(false)
    stopCanvas() // idempotent: does not take the other subscriber's count
    expect(es.closed).toBe(false)
    stopOther()
    expect(es.closed).toBe(true)
    expect(open()).toHaveLength(0)

    const again = recorder(['ai_invoke_finished'])
    const stopAgain = hub('flowgate', again.subscription)
    expect(FakeEventSource.all).toHaveLength(2)
    const second = FakeEventSource.all[1]
    second.emit('open')
    // A fresh connection: its first open is not a reconnect.
    expect(again.resync).not.toHaveBeenCalled()
    // The closed connection's late events reach nobody.
    es.emit('ai_invoke_finished', frame('ai_invoke_finished', {}, 'f.0048'))
    expect(again.frames).toEqual([])
    stopAgain()
    expect(second.closed).toBe(true)
  })

  it('opens one connection per project', () => {
    const stopA = hub('flowgate', recorder(['x']).subscription)
    const stopB = hub('other', recorder(['x']).subscription)
    const stopA2 = canvas('flowgate', { changed: () => {}, resync: () => {} })
    expect(FakeEventSource.all.map((es) => new URL(es.url).searchParams.get('project'))).toEqual(['flowgate', 'other'])
    stopA()
    stopA2()
    expect(FakeEventSource.all.map((es) => es.closed)).toEqual([true, false])
    stopB()
    expect(open()).toHaveLength(0)
  })

  it('reconnect: a drop resyncs every subscriber once; a closed stream reopens with the current token and all names', () => {
    const canvasResync = vi.fn()
    const changed: string[] = []
    const stopCanvas = canvas('flowgate', { changed: (f) => changed.push(f), resync: canvasResync })
    const other = recorder(['doc_review_status_changed'])
    const stopOther = hub('flowgate', other.subscription)
    const first = FakeEventSource.all[0]
    first.emit('open')
    expect([canvasResync.mock.calls.length, other.resync.mock.calls.length]).toEqual([0, 0])

    // Interrupted: the browser retries the same stream itself, the hub only resyncs on its open.
    first.readyState = FakeEventSource.CONNECTING
    first.emit('error')
    first.emit('open')
    expect([canvasResync.mock.calls.length, other.resync.mock.calls.length]).toEqual([1, 1])
    expect(FakeEventSource.all).toHaveLength(1)

    // Closed (e.g. 401 on a rotated token): reopened after the retry wait with the new token.
    sessionStorage.setItem('fg_access_token', 'jwt-2')
    first.readyState = FakeEventSource.CLOSED
    first.emit('error')
    expect(first.closed).toBe(true)
    // A subscriber joining while the reopen waits is listened for on the new connection.
    const late = recorder(['qna_answer_registered'])
    const stopLate = hub('flowgate', late.subscription)
    expect(FakeEventSource.all).toHaveLength(1)
    vi.advanceTimersByTime(PROJECT_STREAM_RETRY_MS - 1)
    expect(FakeEventSource.all).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeEventSource.all).toHaveLength(2)
    const second = FakeEventSource.all[1]
    expect(new URL(second.url).searchParams.get('token')).toBe('jwt-2')
    expect(
      ['graph_canvas_changed', 'flow_ownership_changed', 'doc_review_status_changed', 'qna_answer_registered'].map((t) =>
        second.listened(t),
      ),
    ).toEqual([1, 1, 1, 1])

    second.emit('open')
    expect([canvasResync.mock.calls.length, other.resync.mock.calls.length, late.resync.mock.calls.length]).toEqual([2, 2, 1])
    second.emit('graph_canvas_changed', frame('graph_canvas_changed', { flow_id: 'f.0049' }, 'f.0049'))
    second.emit('doc_review_status_changed', frame('doc_review_status_changed', {}, 'f.0048', 'd.9'))
    second.emit('qna_answer_registered', frame('qna_answer_registered', {}, 'f.0048', 'd.10'))
    expect(changed).toEqual(['f.0049'])
    expect(other.frames.map((f) => f.docId)).toEqual(['d.9'])
    expect(late.frames.map((f) => f.docId)).toEqual(['d.10'])

    stopCanvas()
    stopOther()
    stopLate()
    expect(second.closed).toBe(true)
  })

  it('the last unsubscribe cancels a pending reopen', () => {
    const stopCanvas = canvas('flowgate', { changed: () => {}, resync: () => {} })
    const stopOther = hub('flowgate', recorder(['x']).subscription)
    FakeEventSource.all[0].readyState = FakeEventSource.CLOSED
    FakeEventSource.all[0].emit('error')
    stopCanvas()
    vi.advanceTimersByTime(PROJECT_STREAM_RETRY_MS * 2)
    expect(FakeEventSource.all).toHaveLength(2) // one subscriber left: reopened
    stopOther()
    expect(open()).toHaveLength(0)
    FakeEventSource.all[1].readyState = FakeEventSource.CLOSED
    FakeEventSource.all[1].emit('error')
    vi.advanceTimersByTime(PROJECT_STREAM_RETRY_MS * 2)
    expect(FakeEventSource.all).toHaveLength(2)
  })

  it('a failing subscriber does not keep the event or the resync from the Canvas', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const stopBad = hub('flowgate', {
      events: ['graph_canvas_changed'],
      event: () => {
        throw new Error('boom')
      },
      resync: () => {
        throw new Error('boom')
      },
    })
    const changed: string[] = []
    const resync = vi.fn()
    const stopCanvas = canvas('flowgate', { changed: (f) => changed.push(f), resync })
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('graph_canvas_changed', frame('graph_canvas_changed', { flow_id: 'f.0051' }, 'f.0051'))
    es.readyState = FakeEventSource.CONNECTING
    es.emit('error')
    es.emit('open')
    expect(changed).toEqual(['f.0051'])
    expect(resync).toHaveBeenCalledTimes(1)
    expect(error).toHaveBeenCalledTimes(2)
    stopBad()
    stopCanvas()
  })

  it('passes a frame on as a signal only and drops unreadable data', () => {
    expect(parseProjectEvent('t', 'not json')).toBeNull()
    expect(parseProjectEvent('t', 'null')).toEqual({ type: 't', payload: {}, groupId: null, docId: null })
    expect(parseProjectEvent('t', JSON.stringify({ payload: 'x', group_id: 7, doc_id: '' }))).toEqual({
      type: 't',
      payload: {},
      groupId: null,
      docId: null,
    })
    const rec = recorder(['t'])
    const stop = hub('flowgate', rec.subscription)
    FakeEventSource.all[0].emit('t', 'not json')
    expect(rec.frames).toEqual([])
    stop()
  })

  it('is a no-op without EventSource', () => {
    vi.unstubAllGlobals()
    vi.stubGlobal('EventSource', undefined)
    const stop = hub('flowgate', recorder(['x']).subscription)
    expect(() => stop()).not.toThrow()
  })

  it('the store exposes the hub on the same connection as its Canvas subscription', () => {
    setActivePinia(createPinia())
    const store = useProjectStore()
    const stopCanvas = store.subscribeFlowCanvasChanges('flowgate', { changed: () => {}, resync: () => {} })
    const rec = recorder(['document_explorer_refresh'])
    const stopHub = store.subscribeProjectEvents('flowgate', rec.subscription)
    expect(FakeEventSource.all).toHaveLength(1)
    FakeEventSource.all[0].emit('document_explorer_refresh', frame('document_explorer_refresh', {}, 'f.0048', 'd.1'))
    expect(rec.frames.map((f) => f.docId)).toEqual(['d.1'])
    stopCanvas()
    stopHub()
    expect(open()).toHaveLength(0)
  })
})
