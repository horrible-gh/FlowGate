import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'
import { useTabsStore } from '@main/stores/tabs'

// flowgate.default.0660 T0004 §4 (S2): a tab whose document no longer exists (an AC a Time
// Machine rewind deleted) must not survive in the tab store — the store is persisted to
// localStorage, so a kept tab replays "Document not found" after every reload and re-login.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const showToast = vi.fn()
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const AC_ID = 'flowgate.default.0660.0009-AC'
const KEEP_ID = 'flowgate.default.0660.0005-TR2'

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  showToast.mockReset()
  getRequest.mockReset()
})

function mountFor(id: string) {
  return shallowMount(DocHeader, {
    props: { tab: { id, title: id, path: '', type: 'md', typeCode: 'AC' } as any },
    global: { plugins: [i18n] },
  })
}

describe('DocHeader missing document tab', () => {
  it('closes the tab when the document is gone (404)', async () => {
    const tabs = useTabsStore()
    tabs.openTab({ id: KEEP_ID, title: KEEP_ID, path: '', type: 'md', typeCode: 'TR2' })
    tabs.openTab({ id: AC_ID, title: AC_ID, path: '', type: 'md', typeCode: 'AC' })
    getRequest.mockImplementation((url: string) => (url.includes('/documents/detail')
      ? Promise.reject({ isAxiosError: true, response: { status: 404, data: { detail: `Document not found: ${AC_ID}` } } })
      : Promise.resolve({ data: {} })))

    const wrapper = mountFor(AC_ID)
    await flushPromises()

    expect(tabs.tabs.map((tab) => tab.id)).toEqual([KEEP_ID])
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.doc_header.toast_missing_doc_tab_closed', { docId: AC_ID }), 'warning')
    wrapper.unmount()
  })

  it('keeps the tab on a transient failure', async () => {
    const tabs = useTabsStore()
    tabs.openTab({ id: AC_ID, title: AC_ID, path: '', type: 'md', typeCode: 'AC' })
    getRequest.mockImplementation((url: string) => (url.includes('/documents/detail')
      ? Promise.reject({ isAxiosError: true, response: { status: 503, data: {} } })
      : Promise.resolve({ data: {} })))

    const wrapper = mountFor(AC_ID)
    await flushPromises()

    expect(tabs.tabs.map((tab) => tab.id)).toEqual([AC_ID])
    wrapper.unmount()
  })
})
