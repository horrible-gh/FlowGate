import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'
import { useTabsStore } from '@main/stores/tabs'

// Covers TR0066.0006: the R↔B root-type conversion UI that drives the TR0066.0005
// backend endpoint (PATCH /documents/{doc_id}/root-type). The pill is shown ONLY on a
// pristine workflow root (R/B, editable, undisposed group, undecided); a successful
// convert rewrites the doc_id, so the open tab must follow the new identity.
// See group flowgate.default.0066 (R0001 → NR0003 → TR0005 → T0006 → TR).

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

let typeCode = 'R'
let isEditable = true
let reviewStatus: string | null = 'open'
let headType: string | null = null

const ROOT_ID = 'flowgate.default.0066.0001-R'

function detailResponse() {
  return {
    data: {
      doc_id: ROOT_ID,
      title: '요건정의',
      status: 'open',
      type_code: typeCode,
      doc_review_status: reviewStatus,
      workflow_head_type: headType,
      is_editable: isEditable,
      project_id: 'flowgate',
      group_id: 'flowgate.default.0066',
    },
  }
}

function makeTab() {
  return { id: ROOT_ID, title: '요건정의', path: '', type: 'md', typeCode }
}

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: makeTab() as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  typeCode = 'R'
  isEditable = true
  reviewStatus = 'open'
  headType = null
  getRequest.mockReset()
  patchRequest.mockReset()
  patchRequest.mockResolvedValue({ data: {} })
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader root-type convert visibility', () => {
  it('shows the convert pill (→ B) on a pristine R root', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    const btn = wrapper.find('.doc-convert-btn')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('B')
    wrapper.unmount()
  })

  it('shows the convert pill (→ R) on a pristine B root', async () => {
    typeCode = 'B'
    const wrapper = mountHeader()
    await flushPromises()
    const btn = wrapper.find('.doc-convert-btn')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('R')
    wrapper.unmount()
  })

  it('hides the pill on a non-root document (T)', async () => {
    typeCode = 'T'
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.doc-convert-btn').exists()).toBe(false)
    wrapper.unmount()
  })

  it('hides the pill once the workflow decision is taken (wf_ status)', async () => {
    reviewStatus = 'wf_in_progress'
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).canConvertRootType).toBe(false)
    expect(wrapper.find('.doc-convert-btn').exists()).toBe(false)
    wrapper.unmount()
  })

  it('hides the pill once a workflow head exists (decided via head type)', async () => {
    headType = 'T'
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.doc-convert-btn').exists()).toBe(false)
    wrapper.unmount()
  })

  it('hides the pill when the document is not editable', async () => {
    isEditable = false
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.doc-convert-btn').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('DocHeader root-type convert action', () => {
  it('PATCHes /root-type and moves the open tab to the rewritten doc_id', async () => {
    const tabsStore = useTabsStore()
    tabsStore.openTab(makeTab() as any)
    const newId = 'flowgate.default.0066.0001-B'
    patchRequest.mockResolvedValue({
      data: { data: { doc_id: newId, title: '요건정의', type_code: 'B', project_id: 'flowgate', group_id: 'flowgate.default.0066' } },
    })

    const wrapper = mountHeader()
    await flushPromises()

    // open the confirm, then confirm
    await wrapper.find('.doc-convert-btn').trigger('click')
    await (wrapper.vm as any).doConvertRootType()
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${encodeURIComponent(ROOT_ID)}/root-type`,
      { new_type: 'B' },
    )
    // tab followed the new identity; the stale one is gone
    expect(tabsStore.tabs.find((t) => t.id === newId)).toBeTruthy()
    expect(tabsStore.tabs.find((t) => t.id === ROOT_ID)).toBeFalsy()
    expect(tabsStore.activeTabId).toBe(newId)
    wrapper.unmount()
  })

  it('keeps the tab and surfaces the server message on a 409 (not pristine)', async () => {
    const tabsStore = useTabsStore()
    tabsStore.openTab(makeTab() as any)
    patchRequest.mockRejectedValue({ response: { data: { detail: 'Cannot convert root type after the workflow decision was taken.' } } })

    const wrapper = mountHeader()
    await flushPromises()
    await (wrapper.vm as any).doConvertRootType()
    await flushPromises()

    // the original tab is untouched when the server refuses
    expect(tabsStore.tabs.find((t) => t.id === ROOT_ID)).toBeTruthy()
    wrapper.unmount()
  })
})
