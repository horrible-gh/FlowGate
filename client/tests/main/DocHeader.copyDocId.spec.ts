import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// Covers R0001 group flowgate.default.0132: the document-ID badge is clickable and
// copies the canonical document ID (not the mention block) to the clipboard.

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
}))

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

function detailResponse() {
  return {
    data: {
      doc_id: 'flowgate.default.0132.0004-T',
      title: '작업지시 승인',
      status: 'draft',
      type_code: 'T',
      doc_review_status: 'approved',
      project_id: 'flowgate',
      group_id: 'flowgate.default.0132',
    },
  }
}

function makeTab() {
  return {
    id: 'flowgate.default.0132.0004-T',
    title: '작업지시 승인',
    path: '',
    type: 'md',
    typeCode: 'T',
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
  showToast.mockReset()
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader document-ID copy', () => {
  it('renders the doc-id badge as a clickable button', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    const badge = wrapper.find('button.doc-id-badge')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toContain('flowgate.default.0132.0004-T')
    wrapper.unmount()
  })

  it('copies the canonical doc ID to the clipboard and toasts on success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })

    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('button.doc-id-badge').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('flowgate.default.0132.0004-T')
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.doc_header.toast_doc_id_copied'),
      'success',
    )
    vi.unstubAllGlobals()
    wrapper.unmount()
  })

  it('toasts an error when the clipboard write fails', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    // No execCommand fallback available either → copyToClipboard returns false.
    const execCommand = vi.fn().mockReturnValue(false)
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })

    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('button.doc-id-badge').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.doc_header.toast_copy_failed'),
      'error',
    )
    vi.unstubAllGlobals()
    wrapper.unmount()
  })

  // B0001 group 0134 (load-bearing regression guard): on plain HTTP / LAN-IP origins the page is
  // a NON-secure context, so `navigator.clipboard` is undefined. The old raw
  // `navigator.clipboard.writeText(...)` threw a synchronous TypeError that the .then().catch()
  // chain never caught → silent no-copy, no toast. The fix routes through copyToClipboard(),
  // which must fall back to execCommand('copy') and still report success.
  it('falls back to execCommand and succeeds when navigator.clipboard is unavailable (non-secure context)', async () => {
    // navigator present but WITHOUT a clipboard object — exactly what a browser exposes over
    // http://<lan-ip>:port. Must not throw, must not silently no-op.
    vi.stubGlobal('navigator', {})
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })

    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('button.doc-id-badge').trigger('click')
    await flushPromises()

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.doc_header.toast_doc_id_copied'),
      'success',
    )
    vi.unstubAllGlobals()
    wrapper.unmount()
  })
})
