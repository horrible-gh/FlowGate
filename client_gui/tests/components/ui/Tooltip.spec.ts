import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import Tooltip from '@/components/ui/Tooltip.vue'

// T0004 §5.8/§9: open/close, keyboard/focus. The bubble is teleported to <body>, so it is
// looked up on document rather than inside the mounted wrapper.
describe('components/ui/Tooltip (T0004)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-tooltip').forEach((el) => el.remove())
  })

  it('shows the bubble on mouseenter and hides it on mouseleave', async () => {
    const wrapper = mount(Tooltip, {
      props: { text: '알림 (준비 중)' },
      slots: { default: '<button>bell</button>' },
      attachTo: document.body,
    })

    expect(document.querySelector('.ui-tooltip')).toBeNull()

    await wrapper.get('.ui-tooltip-wrap').trigger('mouseenter')
    expect(document.querySelector('.ui-tooltip')?.textContent).toBe('알림 (준비 중)')

    await wrapper.get('.ui-tooltip-wrap').trigger('mouseleave')
    expect(document.querySelector('.ui-tooltip')).toBeNull()

    wrapper.unmount()
  })

  it('shows on focus and hides on blur (keyboard accessible)', async () => {
    const wrapper = mount(Tooltip, {
      props: { text: '설명' },
      slots: { default: '<button>x</button>' },
      attachTo: document.body,
    })

    await wrapper.get('.ui-tooltip-wrap').trigger('focusin')
    expect(document.querySelector('.ui-tooltip')?.textContent).toBe('설명')

    await wrapper.get('.ui-tooltip-wrap').trigger('focusout')
    expect(document.querySelector('.ui-tooltip')).toBeNull()

    wrapper.unmount()
  })

  it('hides on Escape', async () => {
    const wrapper = mount(Tooltip, {
      props: { text: '설명' },
      slots: { default: '<button>x</button>' },
      attachTo: document.body,
    })

    await wrapper.get('.ui-tooltip-wrap').trigger('mouseenter')
    expect(document.querySelector('.ui-tooltip')).not.toBeNull()

    await wrapper.get('.ui-tooltip-wrap').trigger('keydown', { key: 'Escape' })
    expect(document.querySelector('.ui-tooltip')).toBeNull()

    wrapper.unmount()
  })

  it('never shows a bubble when no text is given', async () => {
    const wrapper = mount(Tooltip, { slots: { default: '<button>x</button>' }, attachTo: document.body })

    await wrapper.get('.ui-tooltip-wrap').trigger('mouseenter')
    expect(document.querySelector('.ui-tooltip')).toBeNull()

    wrapper.unmount()
  })

  it('connects the trigger to the bubble via aria-describedby so a screen reader on the focused control finds it (T0004 §9)', async () => {
    const wrapper = mount(Tooltip, {
      props: { text: '알림 (준비 중)' },
      slots: { default: '<button>bell</button>' },
      attachTo: document.body,
    })

    const trigger = wrapper.get('button')
    expect(trigger.attributes('aria-describedby')).toBeUndefined()

    await wrapper.get('.ui-tooltip-wrap').trigger('focusin')
    const bubble = document.querySelector('.ui-tooltip')
    expect(bubble).not.toBeNull()
    expect(bubble?.id).toBeTruthy()
    expect(trigger.attributes('aria-describedby')).toBe(bubble?.id)

    await wrapper.get('.ui-tooltip-wrap').trigger('focusout')
    expect(trigger.attributes('aria-describedby')).toBeUndefined()

    wrapper.unmount()
  })

  it('preserves a pre-existing aria-describedby on the trigger and only removes its own id (T0004 §9)', async () => {
    const wrapper = mount(Tooltip, {
      props: { text: '알림 (준비 중)' },
      slots: { default: '<button aria-describedby="external-hint">bell</button>' },
      attachTo: document.body,
    })

    const trigger = wrapper.get('button')
    expect(trigger.attributes('aria-describedby')).toBe('external-hint')

    await wrapper.get('.ui-tooltip-wrap').trigger('focusin')
    const bubble = document.querySelector('.ui-tooltip')
    const tokens = trigger.attributes('aria-describedby')?.split(/\s+/) ?? []
    expect(tokens).toContain('external-hint')
    expect(tokens).toContain(bubble?.id)

    await wrapper.get('.ui-tooltip-wrap').trigger('focusout')
    expect(trigger.attributes('aria-describedby')).toBe('external-hint')

    wrapper.unmount()
  })

  it('does not change the trigger layout (wrapper renders no box of its own)', () => {
    const wrapper = mount(Tooltip, { props: { text: 'x' }, slots: { default: '<button>x</button>' } })

    expect(wrapper.get('.ui-tooltip-wrap').element.tagName).toBe('SPAN')
  })
})
