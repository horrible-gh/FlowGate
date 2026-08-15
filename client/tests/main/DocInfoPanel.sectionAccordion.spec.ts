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
    // 0311 T0004 rev1: status · 질의 · AI검수·반려 for a rejected T doc — three headers
    // where the pre-0311 panel had four (status · 질의 · AI검수 · 반려). That is the
    // section-header saving NR0003 §5-4 costed at ≥ ~54px (padding 28 + title row ~26).
    const toggles = wrapper.findAll('.dip-sec-toggle')
    expect(toggles.length).toBe(3)
    expect(wrapper.findAll('.dip-acc-caret').length).toBe(3)
    // none collapsed initially
    expect(wrapper.findAll('.dip-section.collapsed').length).toBe(0)
    // 질의 and AI검수·반려 are two DIFFERENT sections now — the rejection card lives in
    // the merged one, and the queries never mix into it.
    const sections = wrapper.findAll('.dip-section')
    const qaSection = sections.find((s) => s.find('.dip-qa-headline').exists())!
    // rev3: the merged section has no wrapper class of its own — it is found by its
    // title, and its cards are the panel's existing .dip-reject-quote / .dip-ai-entry.
    const mergedSection = sections.find((s) => s.find('.dip-sec-toggle').exists()
      && s.find('.dip-sec-toggle').text().includes(i18n.global.t('main.doc_info_panel.section_review_reject')))!
    expect(qaSection.exists()).toBe(true)
    expect(mergedSection.exists()).toBe(true)
    expect(qaSection.element).not.toBe(mergedSection.element)
    expect(mergedSection.text()).toContain('반려 사유 본문')
    expect(mergedSection.find('.dip-reject-quote').exists()).toBe(true)
    expect(qaSection.text()).not.toContain('반려 사유 본문')
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
