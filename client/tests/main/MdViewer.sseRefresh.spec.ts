import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import MdViewer from '@main/components/MdViewer.vue'

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
    global: { plugins: [i18n] },
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
})
