/**
 * flowgate.v02.0048 T#3 — the Inspector's common read (D#1 §7.2): read the target document,
 * read it again on the caller's server events (target frames only, coalesced, and after a
 * reconnect), loading / error / stale states, a document change drops the previous document's
 * answers, and leaving unsubscribes. The real project event hub runs under the test on the fake
 * EventSource of the shared support module.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, shallowRef, type ShallowRef } from 'vue'
import { subscribeFlowCanvasChanges } from '@/features/project/flowCanvasEvents'
import type { ProjectEventFrame } from '@/features/project'
import {
  INSPECTOR_REFRESH_COALESCE_MS,
  inspectorEventMatches,
  useInspectorEvents,
  useInspectorResource,
  type InspectorEventScope,
  type InspectorResource,
  type InspectorResourceOptions,
} from '@/features/designer/inspector/useInspectorResource'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from './support/projectEventStream'

const DOC_A = 'flowgate.v02.0048.0011-TR'
const DOC_B = 'flowgate.v02.0048.0013-TR'
const FLOW = 'flowgate.v02.0048'
const scopeOf = (docId: string | null, flowId = FLOW, projectId = 'flowgate'): InspectorEventScope => ({ projectId, flowId, docId })

/** A read whose answers the test releases one by one. */
function deferredReads<T>() {
  const calls: Array<{ docId: string; resolve: (value: T) => void; reject: (error: unknown) => void }> = []
  const read = vi.fn(
    (docId: string) =>
      new Promise<T>((resolve, reject) => {
        calls.push({ docId, resolve, reject })
      }),
  )
  return { read, calls }
}

let stream: ProjectEventStream
let pinia: Pinia
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  vi.useFakeTimers()
  stream = installProjectEventStream()
  pinia = createPinia()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.useRealTimers()
})

function mountResource<T>(
  target: ShallowRef<InspectorEventScope | null>,
  options: Omit<InspectorResourceOptions<T>, 'target'>,
  plugins: unknown[] = [pinia],
): InspectorResource<T> {
  let resource: InspectorResource<T> | null = null
  const Host = defineComponent({
    setup() {
      resource = useInspectorResource<T>({ ...options, target: () => target.value })
      return () => h('div')
    },
  })
  wrapper = mount(Host, { global: { plugins: plugins as never[] } })
  return resource!
}

const state = (r: InspectorResource<unknown>) => ({ status: r.status.value, data: r.data.value, stale: r.stale.value, error: r.error.value })

async function passWindow() {
  await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
  await flushPromises()
}

describe('useInspectorResource — reading', () => {
  it('reads the target document: idle without one, loading, then ready', async () => {
    const target = shallowRef<InspectorEventScope | null>(scopeOf(null))
    const { read, calls } = deferredReads<string>()
    const r = mountResource(target, { read, events: ['doc_review_status_changed'] })
    expect(state(r)).toEqual({ status: 'idle', data: null, stale: false, error: null })
    expect(read).not.toHaveBeenCalled()

    target.value = scopeOf(DOC_A)
    await flushPromises()
    expect(read).toHaveBeenCalledWith(DOC_A)
    expect(r.status.value).toBe('loading')
    calls[0].resolve('A1')
    await flushPromises()
    expect(state(r)).toEqual({ status: 'ready', data: 'A1', stale: false, error: null })
  })

  it('a failed first read is an error without data; a manual read again recovers', async () => {
    const failure = Object.assign(new Error('boom'), { response: { status: 500 } })
    const { read, calls } = deferredReads<string>()
    const r = mountResource(shallowRef(scopeOf(DOC_A)), { read, events: [] })
    calls[0].reject(failure)
    await flushPromises()
    expect(state(r)).toEqual({ status: 'error', data: null, stale: false, error: failure })

    const again = r.reload()
    expect(r.status.value).toBe('loading')
    calls[1].resolve('A1')
    await again
    expect(state(r)).toEqual({ status: 'ready', data: 'A1', stale: false, error: null })
  })

  it('a failed read again keeps the last data, marked stale, with the error', async () => {
    const { read, calls } = deferredReads<string>()
    const r = mountResource(shallowRef(scopeOf(DOC_A)), { read, events: [] })
    calls[0].resolve('A1')
    await flushPromises()

    void r.reload()
    expect(state(r)).toEqual({ status: 'loading', data: 'A1', stale: true, error: null })
    const conflict = { isAxiosError: true, response: { status: 409, data: {} } }
    calls[1].reject(conflict)
    await flushPromises()
    expect(state(r)).toEqual({ status: 'error', data: 'A1', stale: true, error: conflict })
  })
})

describe('useInspectorResource — target change', () => {
  it("a slower answer for the previous document never overwrites the new document's data", async () => {
    const target = shallowRef<InspectorEventScope | null>(scopeOf(DOC_A))
    const { read, calls } = deferredReads<string>()
    const r = mountResource(target, { read, events: [] })
    expect(calls.map((c) => c.docId)).toEqual([DOC_A])

    target.value = scopeOf(DOC_B)
    await flushPromises()
    expect(calls.map((c) => c.docId)).toEqual([DOC_A, DOC_B])
    expect(state(r)).toEqual({ status: 'loading', data: null, stale: false, error: null })

    calls[1].resolve('B1')
    await flushPromises()
    calls[0].resolve('A1 (late)')
    await flushPromises()
    expect(state(r)).toEqual({ status: 'ready', data: 'B1', stale: false, error: null })
  })

  it("the previous document's data is dropped at once and its late failure is ignored", async () => {
    const target = shallowRef<InspectorEventScope | null>(scopeOf(DOC_A))
    const { read, calls } = deferredReads<string>()
    const r = mountResource(target, { read, events: [] })
    calls[0].resolve('A1')
    await flushPromises()
    void r.reload()

    target.value = scopeOf(DOC_B)
    await flushPromises()
    expect(r.data.value).toBeNull()
    calls[1].reject(new Error('late failure of A'))
    await flushPromises()
    expect(state(r)).toEqual({ status: 'loading', data: null, stale: false, error: null })
    calls[2].resolve('B1')
    await flushPromises()
    expect(r.data.value).toBe('B1')
  })

  it('an older read again of the same document answered after a newer one is ignored', async () => {
    const { read, calls } = deferredReads<string>()
    const r = mountResource(shallowRef(scopeOf(DOC_A)), { read, events: [] })
    calls[0].resolve('A1')
    await flushPromises()
    void r.reload()
    void r.reload()
    calls[2].resolve('A3')
    await flushPromises()
    calls[1].resolve('A2 (late)')
    await flushPromises()
    expect(state(r)).toEqual({ status: 'ready', data: 'A3', stale: false, error: null })
  })

  it('no target: back to idle with no data, and a pending answer is dropped', async () => {
    const target = shallowRef<InspectorEventScope | null>(scopeOf(DOC_A))
    const { read, calls } = deferredReads<string>()
    const r = mountResource(target, { read, events: [] })
    target.value = null
    await flushPromises()
    calls[0].resolve('A1')
    await flushPromises()
    expect(state(r)).toEqual({ status: 'idle', data: null, stale: false, error: null })
  })
})

describe('useInspectorResource — server events', () => {
  const EVENTS = ['doc_review_status_changed', 'document_explorer_refresh']

  async function ready(target = shallowRef<InspectorEventScope | null>(scopeOf(DOC_A))) {
    const reads = deferredReads<string>()
    const r = mountResource(target, { read: reads.read, events: EVENTS })
    stream.open()
    reads.calls[0].resolve('A1')
    await flushPromises()
    return { r, ...reads, target }
  }

  it('a frame of the target document reads it again once the window has passed; the data is stale meanwhile', async () => {
    const { r, read, calls } = await ready()
    stream.emit('doc_review_status_changed', { docId: DOC_A, groupId: FLOW })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS - 1)
    expect(read).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(read).toHaveBeenCalledTimes(2)
    expect(state(r)).toMatchObject({ status: 'loading', data: 'A1', stale: true })
    calls[1].resolve('A2')
    await flushPromises()
    expect(state(r)).toEqual({ status: 'ready', data: 'A2', stale: false, error: null })
  })

  it('events within the window are one read again', async () => {
    const { read } = await ready()
    stream.emit('doc_review_status_changed', { docId: DOC_A })
    stream.emit('document_explorer_refresh', { docId: DOC_A })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS / 2)
    stream.emit('doc_review_status_changed', { docId: DOC_A })
    await passWindow()
    expect(read).toHaveBeenCalledTimes(2)
    stream.emit('doc_review_status_changed', { docId: DOC_A })
    await passWindow()
    expect(read).toHaveBeenCalledTimes(3)
  })

  it('a Flow-level frame (no doc_id) of the target Flow counts; other documents, Flows and event names do not', async () => {
    const { read } = await ready()
    stream.emit('doc_review_status_changed', { docId: DOC_B, groupId: FLOW })
    stream.emit('document_explorer_refresh', { groupId: 'flowgate.v02.0049' })
    stream.emit('ai_invoke_finished', { docId: DOC_A, groupId: FLOW })
    await passWindow()
    expect(read).toHaveBeenCalledTimes(1)
    stream.emit('document_explorer_refresh', { groupId: FLOW })
    await passWindow()
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('the default match, and a caller match instead of it', async () => {
    const frame = (docId: string | null, groupId: string | null): ProjectEventFrame => ({ type: 'x', payload: {}, docId, groupId })
    expect(inspectorEventMatches(frame(DOC_A, null), scopeOf(DOC_A))).toBe(true)
    expect(inspectorEventMatches(frame(null, FLOW), scopeOf(DOC_A))).toBe(true)
    expect(inspectorEventMatches(frame(null, FLOW), scopeOf(null))).toBe(true)
    expect(inspectorEventMatches(frame(null, null), scopeOf(null))).toBe(false)
    expect(inspectorEventMatches(frame(DOC_B, FLOW), scopeOf(DOC_A))).toBe(false)

    const reads = deferredReads<string>()
    mountResource(shallowRef(scopeOf(DOC_A)), {
      read: reads.read,
      events: ['qna_q_registered'],
      matches: (f) => f.payload.anchor_doc_id === DOC_A,
    })
    stream.emit('qna_q_registered', { docId: DOC_B, payload: { anchor_doc_id: DOC_A } })
    await passWindow()
    expect(reads.read).toHaveBeenCalledTimes(2)
  })

  it('after a reconnect the resource is read again', async () => {
    const { read } = await ready()
    stream.reconnect()
    await passWindow()
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('a pending read again of the previous document is dropped when the target changes', async () => {
    const { read, calls, target } = await ready()
    stream.emit('doc_review_status_changed', { docId: DOC_A })
    target.value = scopeOf(DOC_B)
    await flushPromises()
    await passWindow()
    expect(calls.map((c) => c.docId)).toEqual([DOC_A, DOC_B])
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('events follow the new target after a Node change, on the same connection', async () => {
    const { read, calls, target } = await ready()
    target.value = scopeOf(DOC_B)
    await flushPromises()
    calls[1].resolve('B1')
    await flushPromises()
    stream.emit('doc_review_status_changed', { docId: DOC_A })
    await passWindow()
    expect(read).toHaveBeenCalledTimes(2)
    stream.emit('doc_review_status_changed', { docId: DOC_B })
    await passWindow()
    expect(calls.map((c) => c.docId)).toEqual([DOC_A, DOC_B, DOC_B])
    expect(stream.connections()).toHaveLength(1)
  })

  it('leaving unsubscribes (the last subscriber closes the stream) and drops a pending read again', async () => {
    const { read } = await ready()
    expect(stream.connections()).toHaveLength(1)
    stream.emit('doc_review_status_changed', { docId: DOC_A })
    wrapper!.unmount()
    wrapper = null
    await passWindow()
    expect(read).toHaveBeenCalledTimes(1)
    expect(stream.connections()).toHaveLength(0)
  })

  it('shares the one project connection with the Canvas, which keeps its own events', async () => {
    const changed = vi.fn()
    const stopCanvas = subscribeFlowCanvasChanges('flowgate', { changed, resync: vi.fn() })
    try {
      const { read } = await ready()
      expect(stream.connections()).toHaveLength(1)
      stream.emit('graph_canvas_changed', { groupId: FLOW })
      stream.emit('doc_review_status_changed', { docId: DOC_A })
      await passWindow()
      expect(changed).toHaveBeenCalledTimes(1)
      expect(changed).toHaveBeenCalledWith(FLOW)
      expect(read).toHaveBeenCalledTimes(2)
      wrapper!.unmount()
      wrapper = null
      expect(stream.connections()).toHaveLength(1)
    } finally {
      stopCanvas()
    }
    expect(stream.connections()).toHaveLength(0)
  })

  it('a project change moves the subscription to the new project', async () => {
    const { target } = await ready()
    const first = stream.connections()[0]
    target.value = scopeOf(DOC_A, FLOW, 'other')
    await flushPromises()
    expect(first.closed).toBe(true)
    expect(stream.connections()).toHaveLength(1)
    expect(stream.connections()[0].url).toContain('project=other')
  })
})

describe('useInspectorEvents — without a project store', () => {
  it('a part mounted without Pinia reads but subscribes to nothing', async () => {
    const { read, calls } = deferredReads<string>()
    const r = mountResource(shallowRef(scopeOf(DOC_A)), { read, events: ['doc_review_status_changed'] }, [])
    calls[0].resolve('A1')
    await flushPromises()
    expect(r.data.value).toBe('A1')
    expect(stream.connections()).toHaveLength(0)
  })

  it('resync: false ignores a reconnect but not an event', async () => {
    const changed = vi.fn()
    const Host = defineComponent({
      setup() {
        useInspectorEvents(() => scopeOf(DOC_A), { events: ['doc_review_status_changed'], resync: false, changed })
        return () => h('div')
      },
    })
    wrapper = mount(Host, { global: { plugins: [pinia] } })
    stream.open()
    stream.reconnect()
    await passWindow()
    expect(changed).not.toHaveBeenCalled()
    stream.emit('doc_review_status_changed', { docId: DOC_A })
    await passWindow()
    expect(changed).toHaveBeenCalledTimes(1)
  })
})
