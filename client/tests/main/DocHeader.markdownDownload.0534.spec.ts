import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

const { getRequest, apiGet } = vi.hoisted(() => ({ getRequest: vi.fn(), apiGet: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: vi.fn() },
  getRequest, patchRequest: vi.fn(), postRequest: vi.fn(),
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const tab = { id: 'flowgate.default.0534.0006-TR', title: 'report', path: '', type: 'md', typeCode: 'TR' }
let downloadable = true

function mountHeader(readOnly = false) {
  return shallowMount(DocHeader, { props: { tab: tab as any, readOnly }, global: { plugins: [i18n] } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  apiGet.mockReset()
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve({ data: {
      doc_id: tab.id, title: tab.title, status: 'open', type_code: 'TR',
      project_id: 'flowgate', group_id: 'flowgate.default.0534',
      download_available: downloadable,
    } })
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

// R0001 §3.2 puts the download button immediately left of the document-detail [수정]
// button. That button lives in MainPanel's "문서 내용 미리보기" card (see
// MainPanel.markdownDownloadPlacement.0534.spec.ts), not in DocHeader's title row —
// DocHeader's own title-edit control is an icon-only pencil labelled "타이틀 편집", never
// "수정" (rej_01M1WH0B2S5DWNA8). DocHeader keeps owning the fetch/download mechanics
// (it already loads doc.download_available) and exposes them for MainPanel to drive.
describe('DocHeader current Markdown download (fetch/download mechanics owned here)', () => {
  it('exposes downloadAvailable from the loaded document, and renders no download button itself', async () => {
    downloadable = true
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).downloadAvailable).toBe(true)
    expect(wrapper.find('.doc-markdown-download').exists()).toBe(false)
    wrapper.unmount()
  })

  it('downloadAvailable is false when the server capability is false', async () => {
    downloadable = false
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).downloadAvailable).toBe(false)
    wrapper.unmount()
  })

  it('exposed downloadMarkdown uses the endpoint filename and revokes the object URL', async () => {
    downloadable = true
    const createObjectURL = vi.fn().mockReturnValue('blob:markdown')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    apiGet.mockResolvedValue({
      data: new Blob(['markdown']),
      headers: { 'content-disposition': 'attachment; filename="flowgate.default.0534.0006-TR.md"' },
    })
    const wrapper = mountHeader(true)
    await flushPromises()
    await (wrapper.vm as any).downloadMarkdown()
    await flushPromises()

    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/document/flowgate.default.0534.0006-TR/download',
      { responseType: 'blob' },
    )
    expect(createObjectURL).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:markdown')
    wrapper.unmount()
  })

  it('exposed downloadMarkdown falls back to the full doc_id filename when the header is missing (R0001 §3.3)', async () => {
    downloadable = true
    const createObjectURL = vi.fn().mockReturnValue('blob:markdown')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    apiGet.mockResolvedValue({ data: new Blob(['markdown']), headers: {} })
    const realCreateElement = document.createElement.bind(document)
    const anchor = realCreateElement('a')
    const createElementSpy = vi.spyOn(document, 'createElement')
      .mockImplementation((tag: string) => (tag === 'a' ? anchor : realCreateElement(tag)))

    const wrapper = mountHeader(true)
    await flushPromises()
    await (wrapper.vm as any).downloadMarkdown()
    await flushPromises()

    expect(anchor.download).toBe('flowgate.default.0534.0006-TR.md')
    createElementSpy.mockRestore()
    wrapper.unmount()
  })

  it('exposed downloadMarkdown is a no-op when the server capability is false', async () => {
    downloadable = false
    apiGet.mockClear()
    const wrapper = mountHeader(true)
    await flushPromises()
    await (wrapper.vm as any).downloadMarkdown()
    await flushPromises()
    expect(apiGet).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
