import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Toast from '@/components/ui/Toast.vue'

// T0008 §5/§14: render/tone/dismiss lifecycle, accessibility semantic, no focus stealing.
describe('components/ui/Toast (T0008)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders the caller-supplied message with the default neutral tone', () => {
    const wrapper = mount(Toast, { slots: { default: 'Saved' } })
    expect(wrapper.text()).toContain('Saved')
    expect(wrapper.classes()).toContain('ui-toast--neutral')
  })

  it('applies the requested tone', () => {
    const wrapper = mount(Toast, { props: { tone: 'success' }, slots: { default: 'Copied' } })
    expect(wrapper.classes()).toContain('ui-toast--success')
  })

  it('emits dismiss when the close button is clicked (manual dismiss)', async () => {
    const wrapper = mount(Toast, { slots: { default: 'Saved' } })
    await wrapper.find('.ui-toast-close').trigger('click')
    expect(wrapper.emitted('dismiss')).toHaveLength(1)
  })

  it('auto-dismisses after the default duration', () => {
    const wrapper = mount(Toast, { slots: { default: 'Saved' } })
    expect(wrapper.emitted('dismiss')).toBeUndefined()
    vi.advanceTimersByTime(4000)
    expect(wrapper.emitted('dismiss')).toHaveLength(1)
  })

  it('supports a controlled duration', () => {
    const wrapper = mount(Toast, { props: { duration: 1000 }, slots: { default: 'Saved' } })
    vi.advanceTimersByTime(999)
    expect(wrapper.emitted('dismiss')).toBeUndefined()
    vi.advanceTimersByTime(1)
    expect(wrapper.emitted('dismiss')).toHaveLength(1)
  })

  it('never auto-dismisses when duration is disabled (0 or null)', () => {
    const zero = mount(Toast, { props: { duration: 0 }, slots: { default: 'Pinned' } })
    const nullDuration = mount(Toast, { props: { duration: null }, slots: { default: 'Pinned' } })
    vi.advanceTimersByTime(60000)
    expect(zero.emitted('dismiss')).toBeUndefined()
    expect(nullDuration.emitted('dismiss')).toBeUndefined()
  })

  it('uses role="status"/aria-live="polite" for regular tones and role="alert"/assertive for danger', () => {
    const info = mount(Toast, { props: { tone: 'info' }, slots: { default: 'Info' } })
    expect(info.attributes('role')).toBe('status')
    expect(info.attributes('aria-live')).toBe('polite')

    const danger = mount(Toast, { props: { tone: 'danger', duration: 0 }, slots: { default: 'Failed' } })
    expect(danger.attributes('role')).toBe('alert')
    expect(danger.attributes('aria-live')).toBe('assertive')
  })

  it('never steals keyboard focus, including at auto-dismiss time', () => {
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    const wrapper = mount(Toast, { attachTo: document.body, slots: { default: 'Saved' } })
    expect(document.activeElement).toBe(trigger)
    vi.advanceTimersByTime(4000)
    expect(document.activeElement).toBe(trigger)

    wrapper.unmount()
    trigger.remove()
  })
})
