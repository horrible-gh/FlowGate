import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Tabs from '@/components/ui/Tabs.vue'

const TABS = [
  { key: 'a', label: 'A' },
  { key: 'b', label: 'B' },
  { key: 'c', label: 'C', disabled: true },
]

// T0004 §5.6/§9: active state, change event, disabled tab, no feature-state dependency.
describe('components/ui/Tabs (T0004)', () => {
  it('marks the active tab via aria-selected and a class, purely from the injected modelValue', () => {
    const wrapper = mount(Tabs, { props: { tabs: TABS, modelValue: 'b' } })

    const buttons = wrapper.findAll('button')
    expect(buttons.map((b) => b.attributes('aria-selected'))).toEqual(['false', 'true', 'false'])
    expect(buttons[1].classes()).toContain('is-active')
  })

  it('emits update:modelValue and change when a tab is clicked', async () => {
    const wrapper = mount(Tabs, { props: { tabs: TABS, modelValue: 'a' } })

    await wrapper.findAll('button')[1].trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([['b']])
    expect(wrapper.emitted('change')).toEqual([['b']])
  })

  it('never selects a disabled tab', async () => {
    const wrapper = mount(Tabs, { props: { tabs: TABS, modelValue: 'a' } })

    const disabledTab = wrapper.findAll('button')[2]
    expect(disabledTab.attributes('disabled')).toBeDefined()
    await disabledTab.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('moves selection with ArrowRight/ArrowLeft, skipping disabled tabs', async () => {
    const wrapper = mount(Tabs, { props: { tabs: TABS, modelValue: 'a' } })

    await wrapper.findAll('button')[0].trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['b'])

    await wrapper.findAll('button')[0].trigger('keydown', { key: 'ArrowLeft' })
    // From 'a' (index 0) ArrowLeft wraps to the last enabled tab, which is 'b' (disabled 'c' skipped).
    expect(wrapper.emitted('update:modelValue')?.[1]).toEqual(['b'])
  })

  it('moves DOM focus to the newly selected tab on ArrowRight/ArrowLeft, not just the emitted value (T0004 §5.6/§9)', async () => {
    const wrapper = mount(Tabs, { props: { tabs: TABS, modelValue: 'a' }, attachTo: document.body })

    const buttons = wrapper.findAll('button')
    buttons[0].element.focus()
    expect(document.activeElement).toBe(buttons[0].element)

    await buttons[0].trigger('keydown', { key: 'ArrowRight' })
    await wrapper.vm.$nextTick()
    expect(document.activeElement).toBe(buttons[1].element)

    await buttons[1].trigger('keydown', { key: 'ArrowLeft' })
    await wrapper.vm.$nextTick()
    // 'c' is disabled and excluded from the enabled cycle, so ArrowLeft from 'b' moves back to 'a'.
    expect(document.activeElement).toBe(buttons[0].element)

    wrapper.unmount()
  })

  it('carries no router or feature-state dependency — only tabs/modelValue drive it', () => {
    const wrapper = mount(Tabs, { props: { tabs: TABS, modelValue: 'a', ariaLabel: 'Sections' } })

    expect(wrapper.get('[role="tablist"]').attributes('aria-label')).toBe('Sections')
  })
})
