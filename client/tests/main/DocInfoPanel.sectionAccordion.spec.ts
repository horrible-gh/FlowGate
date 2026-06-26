import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

// R0001 (group 0126 / C안): each info-panel section folds independently under its own
// title caret (the prototype's accordion). The caret toggle must be separate from the
// whole-panel collapse (the `dip-panel-close` chevron / `toggle` emit).

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

function qaResponse() {
  return {
    data: {
      qa: {
        items: [
          { id: 1, seq: 1, title: '질문', body: '본문', asker_kind: 'ai', answer_count: 0, answers: [] },
        ],
      },
    },
  }
}

function mountPanel() {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0126.0002-T',
      typeCode: 'T',
      reviewStatus: 'rejected',
      rejectReason: '반려 사유 본문',
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockResolvedValue(qaResponse())
})

afterEach(() => {
  document.body.querySelectorAll('.modal-qhd').forEach((n) => n.closest('.modal-bg')?.remove())
})

describe('DocInfoPanel section accordion (R0001 group 0126 C안)', () => {
  it('renders a caret toggle on every section, bodies expanded by default', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    // status · Q&A · AI review · rejection are all present for a rejected T doc
    const toggles = wrapper.findAll('.dip-sec-toggle')
    expect(toggles.length).toBe(4)
    expect(wrapper.findAll('.dip-acc-caret').length).toBe(4)
    // none collapsed initially
    expect(wrapper.findAll('.dip-section.collapsed').length).toBe(0)
    // the rejection body is visible while expanded
    expect(wrapper.text()).toContain('반려 사유 본문')
  })

  it('folds and unfolds an individual section without touching the others', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const sections = wrapper.findAll('.dip-section')
    const statusSection = sections[0]

    // collapse the first (status) section via its caret toggle
    await statusSection.find('.dip-sec-toggle').trigger('click')
    expect(statusSection.classes()).toContain('collapsed')
    // only that one section collapsed; the rest stay open
    expect(wrapper.findAll('.dip-section.collapsed').length).toBe(1)

    // toggling again re-expands it
    await statusSection.find('.dip-sec-toggle').trigger('click')
    expect(statusSection.classes()).not.toContain('collapsed')
    expect(wrapper.findAll('.dip-section.collapsed').length).toBe(0)
  })

  it('keeps the whole-panel collapse chevron separate from the accordion', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    // the panel-close button emits `toggle` (whole-panel rail), not a section fold
    await wrapper.find('.dip-panel-close').trigger('click')
    expect(wrapper.emitted('toggle')).toBeTruthy()
    // clicking it did not collapse any individual section
    expect(wrapper.findAll('.dip-section.collapsed').length).toBe(0)
  })
})
