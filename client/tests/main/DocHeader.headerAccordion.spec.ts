import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// R0001 (group 0244): the header's metadata grid folds under a caret at the far RIGHT
// of the first row, next to the ⋯ menu, to reclaim vertical space on tablets.
// Collapsing must NOT hide the document's identity (type chip / doc-id / status /
// title) nor the rejection banner — you still have to know which document you are on,
// and a rejection must not be foldable out of sight.

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function detailResponse(extra: Record<string, unknown> = {}) {
  return {
    data: {
      doc_id: 'flowgate.default.0244.0004-T',
      title: '작업지시 승인',
      status: 'draft',
      type_code: 'T',
      doc_review_status: 'approved',
      project_id: 'flowgate',
      group_id: 'flowgate.default.0244',
      ...extra,
    },
  }
}

function makeTab() {
  return {
    id: 'flowgate.default.0244.0004-T',
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
  localStorage.clear()
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader accordion (R0001 group 0244)', () => {
  it('renders expanded by default with the metadata grid visible', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    const btn = wrapper.find('.doc-hdr-collapse-btn')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.doc-header').classes()).not.toContain('collapsed')
    expect(wrapper.find('.doc-mg').exists()).toBe(true)
  })

  it('toggles the collapsed class and aria-expanded on click', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('.doc-hdr-collapse-btn').trigger('click')
    expect(wrapper.find('.doc-header').classes()).toContain('collapsed')
    expect(wrapper.find('.doc-hdr-collapse-btn').attributes('aria-expanded')).toBe('false')

    await wrapper.find('.doc-hdr-collapse-btn').trigger('click')
    expect(wrapper.find('.doc-header').classes()).not.toContain('collapsed')
    expect(wrapper.find('.doc-hdr-collapse-btn').attributes('aria-expanded')).toBe('true')
  })

  it('persists the collapsed state to localStorage', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('.doc-hdr-collapse-btn').trigger('click')
    expect(localStorage.getItem('flowgate:doc-header:collapsed')).toBe('1')

    await wrapper.find('.doc-hdr-collapse-btn').trigger('click')
    expect(localStorage.getItem('flowgate:doc-header:collapsed')).toBe('0')
  })

  it('restores the collapsed state on mount', async () => {
    localStorage.setItem('flowgate:doc-header:collapsed', '1')
    const wrapper = mountHeader()
    await flushPromises()

    expect(wrapper.find('.doc-header').classes()).toContain('collapsed')
    expect(wrapper.find('.doc-hdr-collapse-btn').attributes('aria-expanded')).toBe('false')
  })

  it('keeps the document identity row and title visible while collapsed', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    await wrapper.find('.doc-hdr-collapse-btn').trigger('click')

    expect(wrapper.find('.doc-meta').exists()).toBe(true)
    expect(wrapper.find('.doc-id-badge').exists()).toBe(true)
    expect(wrapper.find('.doc-status').exists()).toBe(true)
    expect(wrapper.find('.doc-title-row').exists()).toBe(true)
  })

  it('keeps the rejection banner visible while collapsed', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) {
        return Promise.resolve(
          detailResponse({ doc_review_status: 'rejected', rejection_reason: '반려 사유 본문' }),
        )
      }
      if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
      return Promise.resolve({ data: {} })
    })

    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.rejection-banner').exists()).toBe(true)

    await wrapper.find('.doc-hdr-collapse-btn').trigger('click')
    expect(wrapper.find('.doc-header').classes()).toContain('collapsed')
    expect(wrapper.find('.rejection-banner').exists()).toBe(true)
  })

  it('the caret is a sibling of the ⋯ menu button, not nested in it', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    expect(wrapper.find('.doc-hdr-more-btn .doc-hdr-collapse-btn').exists()).toBe(false)
    expect(wrapper.find('.doc-hdr-collapse-btn .doc-hdr-more-btn').exists()).toBe(false)
    expect(wrapper.find('.doc-hdr-actions .doc-hdr-collapse-btn').exists()).toBe(true)
  })

  it('renders the caret even when the ⋯ menu is absent', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) return Promise.resolve(detailResponse({ group_id: null }))
      if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
      return Promise.resolve({ data: {} })
    })

    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.doc-hdr-collapse-btn').exists()).toBe(true)
  })

  it('falls back to expanded when localStorage throws', async () => {
    // Scoped to our own key: other consumers (e.g. the project store) read
    // localStorage unguarded during mount and would fail for unrelated reasons.
    const real = Storage.prototype.getItem
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === 'flowgate:doc-header:collapsed') throw new Error('quota')
      return real.call(this, key)
    })
    try {
      const wrapper = mountHeader()
      await flushPromises()
      expect(wrapper.find('.doc-header').classes()).not.toContain('collapsed')
    } finally {
      spy.mockRestore()
    }
  })
})
