import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'
import { useTabsStore } from '@main/stores/tabs'

// Covers the R0006 work: the title-edit pencil must be available on every document
// type (not just R), gated by editability, and a successful save must keep the open
// tab label in sync. See group flowgate.default.0006 (R0001 → NR0003 → T0004 → TR).

const { getRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest,
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

let typeCode = 'T'
let isEditable = true

function detailResponse() {
  return {
    data: {
      doc_id: 'flowgate.default.0006.0004-T',
      title: '작업지시',
      status: 'draft',
      type_code: typeCode,
      doc_review_status: 'approved',
      is_editable: isEditable,
      project_id: 'flowgate',
      group_id: 'flowgate.default.0006',
    },
  }
}

function makeTab() {
  return {
    id: 'flowgate.default.0006.0004-T',
    title: '작업지시',
    path: '',
    type: 'md',
    typeCode,
  }
}

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: makeTab() as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  typeCode = 'T'
  isEditable = true
  getRequest.mockReset()
  patchRequest.mockReset()
  patchRequest.mockResolvedValue({ data: {} })
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader title edit exposure', () => {
  it('shows the edit pencil on a non-R editable document (T)', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.doc-title-pencil').exists()).toBe(true)
    wrapper.unmount()
  })

  it('hides the edit pencil when the document is not editable', async () => {
    isEditable = false
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).canEditDocument).toBe(false)
    expect(wrapper.find('.doc-title-pencil').exists()).toBe(false)
    wrapper.unmount()
  })

  it('still shows the pencil on an editable R document (unchanged behaviour)', async () => {
    typeCode = 'R'
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.doc-title-pencil').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('DocHeader title save sync', () => {
  it('PATCHes the document and syncs the open tab title', async () => {
    const tabsStore = useTabsStore()
    tabsStore.openTab(makeTab() as any)

    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('.doc-title-pencil').trigger('click')
    await wrapper.find('.doc-title-input').setValue('  새 제목  ')
    await wrapper.find('.doc-title-btn--save').trigger('click')
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${encodeURIComponent('flowgate.default.0006.0004-T')}`,
      { title: '새 제목' },
    )
    // tab label reflects the trimmed saved title
    const tab = tabsStore.tabs.find((t) => t.id === 'flowgate.default.0006.0004-T')
    expect(tab?.title).toBe('새 제목')
    expect(wrapper.emitted('doc-updated')).toBeTruthy()
    wrapper.unmount()
  })

  it('does not PATCH when the trimmed title is empty', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('.doc-title-pencil').trigger('click')
    await wrapper.find('.doc-title-input').setValue('   ')
    await wrapper.find('.doc-title-btn--save').trigger('click')
    await flushPromises()

    expect(patchRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
