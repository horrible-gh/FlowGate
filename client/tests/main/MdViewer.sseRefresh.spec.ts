import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import MdViewer from '@main/components/MdViewer.vue'
import { clearDiagnostics, dumpDiagnostics, recordSseEvent } from '@shared/diagnostics/runtimeDiagnostics'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ default: { get: vi.fn() }, getRequest }))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const DOC = 'test.none.0002.0004-D'
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}
function mountViewer() {
  return shallowMount(MdViewer, {
    props: { path: 'documents/0004-D.md', docId: DOC, projectId: 'test' },
    global: { plugins: [i18n, createPinia()] },
  })
}
function fire(revision: number, docId = DOC, project = 'test') {
  recordSseEvent('document_explorer_refresh')
  window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
    detail: { project, doc_id: docId, revision_no: revision, refresh_key: `${docId}:${revision}` },
  }))
}
async function settleWindow() {
  await vi.advanceTimersByTimeAsync(121)
  await flushPromises()
}
beforeEach(() => {
  vi.useFakeTimers()
  getRequest.mockReset()
  getRequest.mockResolvedValue({ data: { content: 'initial' } })
})
afterEach(() => vi.useRealTimers())

describe('MdViewer SSE content refresh', () => {
  it('coalesces ten updates into one fetch and parse of the final server state', async () => {
    clearDiagnostics()
    const wrapper = mountViewer()
    await flushPromises()
    clearDiagnostics()
    getRequest.mockResolvedValue({ data: { content: 'final revision' } })
    for (let i = 1; i <= 10; i++) fire(i)
    expect(getRequest).toHaveBeenCalledTimes(1)
    await settleWindow()
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect((wrapper.vm as any).content).toBe('final revision')
    expect(dumpDiagnostics().entries.filter((e) => e.type === 'markdown_parse')).toHaveLength(1)
    wrapper.unmount()
  })

  it('runs one trailing fetch after events arrive during an active fetch', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    const first = deferred<{ data: { content: string } }>()
    getRequest.mockImplementationOnce(() => first.promise)
    fire(1)
    await settleWindow()
    for (let i = 2; i <= 10; i++) fire(i)
    expect(getRequest).toHaveBeenCalledTimes(2)
    getRequest.mockResolvedValueOnce({ data: { content: 'latest' } })
    first.resolve({ data: { content: 'older' } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(3)
    expect((wrapper.vm as any).content).toBe('latest')
    wrapper.unmount()
  })

  it('defers rendering while scrolling, then applies the update', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    await wrapper.find('.md-viewer').trigger('scroll')
    getRequest.mockResolvedValueOnce({ data: { content: 'updated after scroll idle' } })
    fire(1)
    await settleWindow()
    expect(getRequest).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect((wrapper.vm as any).content).toBe('updated after scroll idle')
    wrapper.unmount()
  })

  it('applies the latest update within the maximum defer window during continuous scrolling', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    getRequest.mockResolvedValueOnce({ data: { content: 'latest during scroll' } })
    fire(1)
    for (let i = 0; i < 8; i++) {
      await wrapper.find('.md-viewer').trigger('scroll')
      await vi.advanceTimersByTimeAsync(100)
    }
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect((wrapper.vm as any).content).toBe('latest during scroll')
    wrapper.unmount()
  })

  it('drops a delayed callback after switching documents or unmounting', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    fire(1)
    await wrapper.setProps({ docId: 'test.none.0002.0005-D' })
    await flushPromises()
    await settleWindow()
    expect(getRequest).toHaveBeenCalledTimes(2) // initial + new document
    fire(2, 'test.none.0002.0005-D')
    wrapper.unmount()
    await settleWindow()
    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('keeps the matching SSE recovery attribution and ignores other projects', async () => {
    clearDiagnostics()
    const wrapper = mountViewer()
    await flushPromises()
    clearDiagnostics()
    fire(1, DOC, 'other')
    await settleWindow()
    expect(getRequest).toHaveBeenCalledTimes(1)
    fire(2)
    await settleWindow()
    const parses = dumpDiagnostics().entries.filter((e) => e.type === 'markdown_parse') as any[]
    expect(parses).toHaveLength(1)
    expect(parses[0].lastRecoverySource).toBe('sse_event:document_explorer_refresh')
    wrapper.unmount()
  })

  it('reports a failed refresh and accepts the next successful update', async () => {
    const completed = vi.fn()
    window.addEventListener('fg:document_content_refresh_completed', completed)
    const wrapper = mountViewer()
    await flushPromises()
    getRequest.mockRejectedValueOnce(new Error('network down'))
    fire(1)
    await settleWindow()
    expect((completed.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      doc_id: DOC, revision_no: 1, success: false,
    })
    getRequest.mockResolvedValueOnce({ data: { content: 'recovered' } })
    fire(2)
    await settleWindow()
    expect((wrapper.vm as any).content).toBe('recovered')
    expect((completed.mock.calls[1][0] as CustomEvent).detail).toMatchObject({
      doc_id: DOC, revision_no: 2, success: true,
    })
    wrapper.unmount()
    window.removeEventListener('fg:document_content_refresh_completed', completed)
  })
})
