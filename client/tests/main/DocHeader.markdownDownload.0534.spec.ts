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

describe('DocHeader current Markdown download', () => {
  it('shows immediately left of edit and remains available in read-only mode', async () => {
    downloadable = true
    const editable = mountHeader()
    await flushPromises()
    const rowButtons = editable.findAll('.doc-title-row button')
    expect(rowButtons[0].classes()).toContain('doc-markdown-download')
    expect(rowButtons[1].classes()).toContain('doc-title-pencil')
    editable.unmount()

    const readOnly = mountHeader(true)
    await flushPromises()
    expect(readOnly.find('.doc-markdown-download').exists()).toBe(true)
    expect(readOnly.findAll('.doc-title-row button')).toHaveLength(1)
    readOnly.unmount()
  })

  it('uses the endpoint filename and revokes the object URL', async () => {
    downloadable = true
    const createObjectURL = vi.fn().mockReturnValue('blob:markdown')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    apiGet.mockResolvedValue({
      data: new Blob(['markdown']),
      headers: { 'content-disposition': 'attachment; filename="TR0006.md"' },
    })
    const wrapper = mountHeader(true)
    await flushPromises()
    await wrapper.get('.doc-markdown-download').trigger('click')
    await flushPromises()

    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/document/flowgate.default.0534.0006-TR/download',
      { responseType: 'blob' },
    )
    expect(createObjectURL).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:markdown')
    wrapper.unmount()
  })

  it('hides the action when the server capability is false', async () => {
    downloadable = false
    const wrapper = mountHeader(true)
    await flushPromises()
    expect(wrapper.find('.doc-markdown-download').exists()).toBe(false)
    wrapper.unmount()
  })
})
