import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const defaultProps = {
  docId: 'test.doc',
  projectId: 'test-project',
  groupId: 'test-group',
  docRef: 'test-ref',
  reviewStatus: null,
}

function domRect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockResolvedValue({
    data: { ok: true, state: { branch: null, status: 'none', default_action: null, choices: [] } },
  })
  postRequest.mockResolvedValue({ data: { document: { doc_review_status: 'approved' } } })
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 })
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('ReviewActionBar teleported drop-up geometry', () => {
  const cases = [
    {
      name: 'workflow',
      props: { ...defaultProps, mode: 'workflow' as const, docType: 'R' },
      trigger: '.ab-dd-toggle',
      triggerRight: 108.2,
      menuHeight: 130,
    },
    {
      name: 'next',
      props: { ...defaultProps, mode: 'next' as const, docType: 'R', nextStepCode: 'D' },
      trigger: '.ab-dd-toggle',
      triggerRight: 95.7,
      menuHeight: 130,
    },
    {
      name: 'next test-report pending',
      props: {
        ...defaultProps,
        mode: 'next' as const,
        docType: 'TS',
        nextStepCode: 'TSR',
        testRunStatus: null,
      },
      trigger: '.ab-split-caret',
      triggerRight: 112.7,
      menuHeight: 66,
    },
    {
      name: 'review request',
      props: {
        ...defaultProps,
        mode: 'review' as const,
        docType: 'D',
        reviewStatus: 'pending_review',
      },
      trigger: '.ab-split-caret',
      triggerRight: 198.8,
      menuHeight: 98,
    },
  ]

  it.each(cases)('teleports and clamps the $name menu at 375px', async scenario => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      if (this.classList.contains('ab-split-dd')) return domRect(0, 0, 140, scenario.menuHeight)
      if (this.matches(scenario.trigger)) return domRect(scenario.triggerRight - 28, 773, 28, 29)
      return domRect(0, 0, 0, 0)
    })

    const wrapper = mount(ReviewActionBar, {
      props: scenario.props,
      global: { plugins: [i18n], stubs: { teleport: false } },
    })

    await wrapper.find(scenario.trigger).trigger('click')
    await flushPromises()

    const menu = document.body.querySelector<HTMLElement>(':scope > .ab-split-dd')
    expect(menu).not.toBeNull()
    expect(menu?.parentElement).toBe(document.body)
    const expectedLeft = Math.max(8, Math.min(scenario.triggerRight - 140, 375 - 8 - 140))
    expect(menu?.style.left).toBe(`${expectedLeft}px`)
    expect(menu?.style.top).toBe(`${Math.max(8, 773 - 6 - scenario.menuHeight)}px`)

    menu?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(document.body.querySelector('.ab-split-dd')).not.toBeNull()

    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()
    expect(document.body.querySelector('.ab-split-dd')).toBeNull()
    wrapper.unmount()
  })

  it.each(cases)('preserves the $name trigger-right alignment at desktop width', async scenario => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      if (this.classList.contains('ab-split-dd')) return domRect(0, 0, 140, scenario.menuHeight)
      if (this.matches(scenario.trigger)) return domRect(1228, 750, 28, 29)
      return domRect(0, 0, 0, 0)
    })

    const wrapper = mount(ReviewActionBar, {
      props: scenario.props,
      global: { plugins: [i18n], stubs: { teleport: false } },
    })
    await wrapper.find(scenario.trigger).trigger('click')
    await flushPromises()

    const menu = document.body.querySelector<HTMLElement>(':scope > .ab-split-dd')
    expect(Number.parseFloat(menu?.style.left ?? 'NaN') + 140).toBe(1256)
    wrapper.unmount()
  })

  it('keeps the 8px left floor when the viewport is narrower than the menu', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 120 })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      if (this.classList.contains('ab-split-dd')) return domRect(0, 0, 140, 66)
      if (this.classList.contains('ab-dd-toggle')) return domRect(4, 200, 80, 29)
      return domRect(0, 0, 0, 0)
    })

    const wrapper = mount(ReviewActionBar, {
      props: { ...defaultProps, mode: 'next', docType: 'R', nextStepCode: 'D' },
      global: { plugins: [i18n], stubs: { teleport: false } },
    })
    await wrapper.find('.ab-dd-toggle').trigger('click')
    await flushPromises()
    expect(document.body.querySelector<HTMLElement>('.ab-split-dd')?.style.left).toBe('8px')
    wrapper.unmount()
  })
})