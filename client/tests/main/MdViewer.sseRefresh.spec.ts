import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import MdViewer from '@main/components/MdViewer.vue'
import { clearDiagnostics, dumpDiagnostics, recordSseEvent } from '@shared/diagnostics/runtimeDiagnostics'

// A promise this test controls the settlement of, so two overlapping loadContent() calls
// can be made to *resolve* in the opposite order they *started* in — the exact shape of the
// race rev4 finding 1 describes (0616 T0004 rev4).
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { get: vi.fn() },
  getRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function mountViewer() {
  return shallowMount(MdViewer, {
    props: {
      path: 'D:/documents/0004-D_document.md',
      docId: 'test.none.0002.0004-D',
      projectId: 'test',
    },
    global: { plugins: [i18n, createPinia()] },
  })
}

beforeEach(() => {
  getRequest.mockReset()
  getRequest
    .mockResolvedValueOnce({ data: { content: 'revision 0' } })
    .mockResolvedValue({ data: { content: 'revision 1' } })
})

describe('MdViewer SSE content refresh', () => {
  it('reloads an open document when its content-changed event arrives', async () => {
    const completed = vi.fn()
    window.addEventListener('fg:document_content_refresh_completed', completed)
    const wrapper = mountViewer()
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect((wrapper.vm as any).content).toBe('revision 0')

    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: {
        project: 'test',
        doc_id: 'test.none.0002.0004-D',
        revision_no: 1,
        refresh_key: 'test.none.0002.0004-D:1',
      },
    }))
    await flushPromises()

    expect(getRequest).toHaveBeenCalledTimes(2)
    expect((wrapper.vm as any).content).toBe('revision 1')
    expect((completed.mock.calls[0][0] as CustomEvent).detail).toEqual({
      doc_id: 'test.none.0002.0004-D',
      revision_no: 1,
      refresh_key: 'test.none.0002.0004-D:1',
      success: true,
    })
    wrapper.unmount()
    window.removeEventListener('fg:document_content_refresh_completed', completed)
  })

  it('ignores events for another document or project', async () => {
    const wrapper = mountViewer()
    await flushPromises()

    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { project: 'test', doc_id: 'test.none.0002.9999-D' },
    }))
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { project: 'other', doc_id: 'test.none.0002.0004-D' },
    }))
    await flushPromises()

    expect(getRequest).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('stops listening after unmount', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    wrapper.unmount()

    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { project: 'test', doc_id: 'test.none.0002.0004-D' },
    }))
    await flushPromises()

    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  // 0616 T0004 rev3 finding 2: recordMarkdownParse() must attribute a parse to the cause the
  // caller actually knew about at reload-start time, not to whatever the diagnostics module's
  // ambient globals happen to hold when the parse eventually runs.
  it('attributes the initial (non-SSE) parse to no recovery source and no epoch', async () => {
    clearDiagnostics()
    // A prior, unrelated SSE event must not bleed into a parse that this component's own
    // watch(immediate:true)-driven initial load causes.
    recordSseEvent('ping')
    const wrapper = mountViewer()
    await flushPromises()

    const parses = dumpDiagnostics().entries.filter((e) => e.type === 'markdown_parse')
    expect(parses).toHaveLength(1)
    expect(parses[0]).toMatchObject({ lastRecoverySource: null, lastRecoverySourceAgeMs: null, refreshEpoch: null })
    wrapper.unmount()
  })

  it('attributes an SSE-triggered reload parse to the SSE event that caused it, with no epoch', async () => {
    clearDiagnostics()
    const wrapper = mountViewer()
    await flushPromises()
    clearDiagnostics()

    // Mirrors useFlowGateSse.ts's on() wrapper: recordSseEvent() runs synchronously in the
    // real SSE event handler, before it dispatches fg:document_content_changed.
    recordSseEvent('document_explorer_refresh')
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: {
        project: 'test',
        doc_id: 'test.none.0002.0004-D',
        revision_no: 1,
        refresh_key: 'test.none.0002.0004-D:1',
      },
    }))
    await flushPromises()

    const parses = dumpDiagnostics().entries.filter((e) => e.type === 'markdown_parse')
    expect(parses).toHaveLength(1)
    const entry = parses[0] as any
    expect(entry.lastRecoverySource).toBe('sse_event:document_explorer_refresh')
    expect(entry.refreshEpoch).toBeNull()
    expect(entry.lastRecoverySourceAgeMs).not.toBeNull()
    expect(entry.lastRecoverySourceAgeMs).toBeGreaterThanOrEqual(0)
    wrapper.unmount()
  })

  // 0616 T0004 rev4 finding 1: rev3's fix still routed the cause through a single shared
  // ref written synchronously at each call's start and read only later, when the parse
  // actually ran — so a second, overlapping loadContent() call could overwrite that ref
  // before the first call's own fetch resolved, and the first call's parse would then be
  // recorded under the second call's cause. Both tests below force the resolution order to
  // be the *reverse* of the start order — the exact shape rev3's own tests never exercised
  // (they only ever awaited one loadContent() to completion before starting the next).
  it('attributes each of two overlapping SSE reloads to its own cause, even when the earlier one resolves last', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    clearDiagnostics()

    const first = deferred<{ data: { content: string } }>()
    const second = deferred<{ data: { content: string } }>()
    getRequest.mockImplementationOnce(() => first.promise)
    getRequest.mockImplementationOnce(() => second.promise)

    // Event 1 starts a reload (call #1) — its handler runs to the `await getRequest(...)`
    // synchronously, exactly like the real SSE `on()` wrapper (recordSseEvent then dispatch).
    recordSseEvent('document_explorer_refresh')
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: {
        project: 'test',
        doc_id: 'test.none.0002.0004-D',
        revision_no: 1,
        refresh_key: 'test.none.0002.0004-D:1',
      },
    }))
    // Event 2 arrives before call #1's fetch has resolved (call #2 starts).
    recordSseEvent('doc_review_status_changed')
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: {
        project: 'test',
        doc_id: 'test.none.0002.0004-D',
        revision_no: 2,
        refresh_key: 'test.none.0002.0004-D:2',
      },
    }))

    // Resolve call #2 (the later-starting one) FIRST — the reverse of start order.
    second.resolve({ data: { content: 'revision 2' } })
    await flushPromises()
    first.resolve({ data: { content: 'revision 1' } })
    await flushPromises()

    const parses = dumpDiagnostics().entries.filter((e) => e.type === 'markdown_parse') as any[]
    expect(parses).toHaveLength(2)
    // Recorded in resolution order: call #2 finished first, so its own cause
    // (doc_review_status_changed) must be the first entry, not call #1's.
    expect(parses[0].lastRecoverySource).toBe('sse_event:doc_review_status_changed')
    expect(parses[1].lastRecoverySource).toBe('sse_event:document_explorer_refresh')
    wrapper.unmount()
  })

  it('does not let a manual reload starting after an SSE reload steal that SSE reload\'s cause when the manual one resolves first', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    clearDiagnostics()

    const sse = deferred<{ data: { content: string } }>()
    const manual = deferred<{ data: { content: string } }>()
    getRequest.mockImplementationOnce(() => sse.promise)
    getRequest.mockImplementationOnce(() => manual.promise)

    recordSseEvent('document_explorer_refresh')
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: {
        project: 'test',
        doc_id: 'test.none.0002.0004-D',
        revision_no: 1,
        refresh_key: 'test.none.0002.0004-D:1',
      },
    }))
    // A manual/prop reload (cause=null, MdViewer's default) starts while the SSE reload
    // above is still in flight — e.g. the user re-opens the same document from the explorer.
    void (wrapper.vm as any).loadContent()

    // The manual call resolves FIRST even though it started second.
    manual.resolve({ data: { content: 'manual reload' } })
    await flushPromises()
    sse.resolve({ data: { content: 'revision 1' } })
    await flushPromises()

    const parses = dumpDiagnostics().entries.filter((e) => e.type === 'markdown_parse') as any[]
    expect(parses).toHaveLength(2)
    expect(parses[0]).toMatchObject({ lastRecoverySource: null, refreshEpoch: null })
    expect(parses[1].lastRecoverySource).toBe('sse_event:document_explorer_refresh')
    wrapper.unmount()
  })
})
