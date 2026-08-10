import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import type { StepState } from '@main/workflow/workflowViewState'

// R0001 (group 0244): the sequence strip folds under a caret at the far RIGHT of the
// section title, because .wf-flow wraps without a height cap and eats the tablet's
// scarce vertical space. The caret must not interfere with [시퀀스 수정], which sits
// next to it in the same title row.

function ss(code: string): StepState {
  return { code, visual: 'done', className: 'done', iconClass: 'check-circle' }
}

function mountComp(overrides: Record<string, unknown> = {}) {
  return mount(DocWorkflow, {
    props: {
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [ss('R'), ss('D')] as StepState[],
      ...overrides,
    } as any,
    global: {
      plugins: [i18n],
      stubs: { WorkflowDecisionModal: true },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  localStorage.clear()
})

describe('DocWorkflow sequence accordion (R0001 group 0244)', () => {
  it('renders expanded by default with the flow visible', () => {
    const wrapper = mountComp()
    const btn = wrapper.find('.wf-collapse-btn')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.wf-section').classes()).not.toContain('collapsed')
    expect(wrapper.find('.wf-flow').exists()).toBe(true)
  })

  it('toggles the collapsed class and aria-expanded on click', async () => {
    const wrapper = mountComp()
    await wrapper.find('.wf-collapse-btn').trigger('click')

    expect(wrapper.find('.wf-section').classes()).toContain('collapsed')
    expect(wrapper.find('.wf-collapse-btn').attributes('aria-expanded')).toBe('false')

    await wrapper.find('.wf-collapse-btn').trigger('click')
    expect(wrapper.find('.wf-section').classes()).not.toContain('collapsed')
    expect(wrapper.find('.wf-collapse-btn').attributes('aria-expanded')).toBe('true')
  })

  it('persists the collapsed state to localStorage', async () => {
    const wrapper = mountComp()
    await wrapper.find('.wf-collapse-btn').trigger('click')
    expect(localStorage.getItem('flowgate:doc-workflow:collapsed')).toBe('1')

    await wrapper.find('.wf-collapse-btn').trigger('click')
    expect(localStorage.getItem('flowgate:doc-workflow:collapsed')).toBe('0')
  })

  it('restores the collapsed state on mount', () => {
    localStorage.setItem('flowgate:doc-workflow:collapsed', '1')
    const wrapper = mountComp()
    expect(wrapper.find('.wf-section').classes()).toContain('collapsed')
    expect(wrapper.find('.wf-collapse-btn').attributes('aria-expanded')).toBe('false')
  })

  it('keeps the section title visible while collapsed', async () => {
    const wrapper = mountComp()
    await wrapper.find('.wf-collapse-btn').trigger('click')
    expect(wrapper.find('.sec-title').exists()).toBe(true)
    expect(wrapper.find('.sec-title').text()).toContain('Workflow')
  })

  it('[Edit Sequence] is a sibling of the caret, not nested inside it', () => {
    const wrapper = mountComp()
    expect(wrapper.find('.wf-collapse-btn .wf-edit-btn').exists()).toBe(false)
    expect(wrapper.find('.wf-edit-btn .wf-collapse-btn').exists()).toBe(false)
  })

  it('clicking [Edit Sequence] opens the modal without folding the section', async () => {
    const wrapper = mountComp()
    await wrapper.find('.wf-edit-btn').trigger('click')

    expect(wrapper.find('.wf-section').classes()).not.toContain('collapsed')
    expect(wrapper.find('.wf-collapse-btn').attributes('aria-expanded')).toBe('true')
    expect(localStorage.getItem('flowgate:doc-workflow:collapsed')).toBeNull()
  })

  it('falls back to expanded when localStorage throws', () => {
    // Scoped to our own key so unrelated localStorage consumers still work.
    const real = Storage.prototype.getItem
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === 'flowgate:doc-workflow:collapsed') throw new Error('quota')
      return real.call(this, key)
    })
    try {
      const wrapper = mountComp()
      expect(wrapper.find('.wf-section').classes()).not.toContain('collapsed')
    } finally {
      spy.mockRestore()
    }
  })
})


describe('DocWorkflow sequence accordion readOnly (0404)', () => {
  it('keeps collapse/expand local behavior while hiding sequence editing', async () => {
    const wrapper = mountComp({ readOnly: true })

    expect(wrapper.find('.wf-edit-btn').exists()).toBe(false)
    const collapse = wrapper.find('.wf-collapse-btn')
    expect(collapse.exists()).toBe(true)
    expect(collapse.attributes('aria-expanded')).toBe('true')

    await collapse.trigger('click')
    expect(wrapper.find('.wf-section').classes()).toContain('collapsed')
    expect(collapse.attributes('aria-expanded')).toBe('false')

    await collapse.trigger('click')
    expect(wrapper.find('.wf-section').classes()).not.toContain('collapsed')
    expect(collapse.attributes('aria-expanded')).toBe('true')
  })
})
