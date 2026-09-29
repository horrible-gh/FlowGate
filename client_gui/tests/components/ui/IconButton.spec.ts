import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import IconButton from '@/components/ui/IconButton.vue'

// T0004 §5.2/§9: icon-only control — render, click, disabled, keyboard focus, accessible label.
describe('components/ui/IconButton (T0004)', () => {
  it('requires an accessible label and renders it as aria-label', () => {
    const wrapper = mount(IconButton, { props: { label: '알림' }, slots: { default: '<svg />' } })

    const el = wrapper.get('button')
    expect(el.attributes('aria-label')).toBe('알림')
    expect(el.attributes('type')).toBe('button')
  })

  it('does not render a native title unless one is explicitly given', () => {
    const withoutTitle = mount(IconButton, { props: { label: '알림' } })
    expect(withoutTitle.get('button').attributes('title')).toBeUndefined()

    const withTitle = mount(IconButton, { props: { label: '알림', title: '알림 (준비 중)' } })
    expect(withTitle.get('button').attributes('title')).toBe('알림 (준비 중)')
  })

  it('emits a click when enabled', async () => {
    const wrapper = mount(IconButton, { props: { label: '닫기' } })

    await wrapper.trigger('click')

    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('is disabled and blocks click when disabled', async () => {
    const wrapper = mount(IconButton, { props: { label: '닫기', disabled: true } })

    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('applies the requested size/variant classes', () => {
    const wrapper = mount(IconButton, { props: { label: '설정', size: 'lg', variant: 'secondary' } })

    expect(wrapper.classes()).toContain('ui-icon-btn--lg')
    expect(wrapper.classes()).toContain('ui-icon-btn--secondary')
  })
})
