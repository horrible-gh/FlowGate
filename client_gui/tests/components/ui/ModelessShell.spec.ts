import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'

// T0006 §6/§12: open/close, no backdrop, no focus trap, drag, resize, minimize/restore,
// viewport boundary, background interaction stays live, bring-to-front. Teleported to
// <body> (T0006 §6 decided this the same way Tooltip/Dropdown already do, to avoid
// ancestor overflow/z-index fights — see the TR for the recorded reasoning).
describe('components/ui/ModelessShell (T0006)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-modeless-panel').forEach((el) => el.remove())
  })

  function mountPanel(props: Record<string, unknown> = {}) {
    return mount(ModelessShell, {
      props: { open: true, title: 'Panel', initialX: 100, initialY: 100, initialWidth: 400, initialHeight: 300, ...props },
      slots: { default: '<div class="panel-body">content</div>' },
      attachTo: document.body,
    })
  }

  it('renders no backdrop element (background stays interactive)', () => {
    const wrapper = mountPanel()
    expect(document.querySelector('.ui-modal-overlay')).toBeNull()
    expect(document.querySelector('.ui-modeless-panel')).not.toBeNull()
    wrapper.unmount()
  })

  it('positions itself from the initial x/y/width/height props', () => {
    const wrapper = mountPanel()
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement
    expect(panel.style.left).toBe('100px')
    expect(panel.style.top).toBe('100px')
    expect(panel.style.width).toBe('400px')
    wrapper.unmount()
  })

  it('drags via the titlebar and updates its position', async () => {
    const wrapper = mountPanel()
    const titlebar = document.querySelector('.ui-modeless-titlebar') as HTMLElement
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement

    titlebar.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 200, clientY: 200 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 250, clientY: 240 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(panel.style.left).toBe('150px')
    expect(panel.style.top).toBe('140px')
    wrapper.unmount()
  })

  it('does not drag when draggable is false', async () => {
    const wrapper = mountPanel({ draggable: false })
    const titlebar = document.querySelector('.ui-modeless-titlebar') as HTMLElement
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement

    titlebar.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 200, clientY: 200 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 250, clientY: 240 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(panel.style.left).toBe('100px')
    wrapper.unmount()
  })

  it('clamps position so the panel cannot fully leave the viewport', async () => {
    const wrapper = mountPanel()
    const titlebar = document.querySelector('.ui-modeless-titlebar') as HTMLElement
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement

    titlebar.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: -5000, clientY: -5000 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(parseFloat(panel.style.top)).toBeGreaterThanOrEqual(0)
    expect(parseFloat(panel.style.left)).toBeGreaterThan(-400)
    wrapper.unmount()
  })

  it('clamps an initial position that starts off-screen back into the viewport when opened', async () => {
    const wrapper = mount(ModelessShell, {
      props: { open: false, title: 'Panel', initialX: 5000, initialY: 5000, initialWidth: 400, initialHeight: 300 },
      attachTo: document.body,
    })
    await wrapper.setProps({ open: true })
    await wrapper.vm.$nextTick()

    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement
    expect(parseFloat(panel.style.left)).toBeLessThan(5000)
    expect(parseFloat(panel.style.top)).toBeLessThan(5000)
    wrapper.unmount()
  })

  it('re-clamps position when the viewport shrinks while the panel is open', async () => {
    const wrapper = mountPanel({ initialX: 900, initialY: 100, initialWidth: 400 })
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement
    expect(panel.style.left).toBe('900px')

    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 500 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()

    expect(parseFloat(panel.style.left)).toBeLessThanOrEqual(500 - 80)

    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
    wrapper.unmount()
  })

  it('resizes via the resize handle, bounded by minWidth/minHeight', async () => {
    const wrapper = mountPanel({ minWidth: 240, minHeight: 160 })
    const handle = document.querySelector('.ui-modeless-resize-handle') as HTMLElement
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 500, clientY: 400 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 400, clientY: 300 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(panel.style.width).toBe('300px')
    expect(panel.style.height).toBe('200px')

    // shrink far past the minimum
    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 400, clientY: 300 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: -1000, clientY: -1000 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(panel.style.width).toBe('240px')
    expect(panel.style.height).toBe('160px')
    wrapper.unmount()
  })

  it('minimizes and restores, hiding the body while minimized', async () => {
    const wrapper = mountPanel()
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement
    const minimizeBtn = document.querySelectorAll('.ui-modeless-titlebar-actions button')[0] as HTMLElement
    const body = () => document.querySelector('.ui-modeless-body') as HTMLElement

    expect(body().style.display).not.toBe('none')
    minimizeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(panel.classList.contains('is-minimized')).toBe(true)
    expect(body().style.display).toBe('none')

    minimizeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(panel.classList.contains('is-minimized')).toBe(false)
    expect(body().style.display).not.toBe('none')
    wrapper.unmount()
  })

  // flowgate.v02.0033 C1: an optional controlled `minimized` (v-model:minimized).
  it('follows a controlled minimized prop and reports every toggle', async () => {
    const wrapper = mountPanel({ minimized: true })
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement
    const minimizeBtn = document.querySelectorAll('.ui-modeless-titlebar-actions button')[0] as HTMLElement
    expect(panel.classList.contains('is-minimized')).toBe(true)

    minimizeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:minimized')).toEqual([[false]])
    // Controlled: the caller has not applied the change yet, so the shell stays minimized.
    expect(panel.classList.contains('is-minimized')).toBe(true)

    await wrapper.setProps({ minimized: false })
    expect(panel.classList.contains('is-minimized')).toBe(false)
    await wrapper.setProps({ minimized: true })
    expect(panel.classList.contains('is-minimized')).toBe(true)
    wrapper.unmount()
  })

  it('still reports toggles while uncontrolled', async () => {
    const wrapper = mountPanel()
    const minimizeBtn = document.querySelectorAll('.ui-modeless-titlebar-actions button')[0] as HTMLElement
    minimizeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:minimized')).toEqual([[true]])
    wrapper.unmount()
  })

  it('emits close when the close control is used', async () => {
    const wrapper = mountPanel()
    const closeBtn = document.querySelectorAll('.ui-modeless-titlebar-actions button')[1] as HTMLElement
    closeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not trap Tab focus (no focus trap by design)', () => {
    const wrapper = mountPanel()
    const panel = document.querySelector('.ui-modeless-panel') as HTMLElement
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    panel.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
    wrapper.unmount()
  })

  it('keeps the base screen interactive: focus outside the panel is left alone while it is open', () => {
    const outside = document.createElement('button')
    outside.className = 'outside-btn'
    document.body.appendChild(outside)

    const wrapper = mountPanel()
    outside.focus()
    expect(document.activeElement).toBe(outside)

    wrapper.unmount()
    outside.remove()
  })

  it('brings itself to front (raises z-index) on mousedown, higher than a panel opened before it', async () => {
    const first = mountPanel()
    const second = mountPanel()
    const firstPanel = document.querySelectorAll('.ui-modeless-panel')[0] as HTMLElement
    const secondPanel = document.querySelectorAll('.ui-modeless-panel')[1] as HTMLElement
    const zBefore = Number(firstPanel.style.zIndex)

    firstPanel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await first.vm.$nextTick()

    expect(Number(firstPanel.style.zIndex)).toBeGreaterThan(zBefore)
    expect(Number(firstPanel.style.zIndex)).toBeGreaterThan(Number(secondPanel.style.zIndex))

    first.unmount()
    second.unmount()
  })

  // flowgate.v02.0088 F06: the shell's own controls are named in the app's current language (gui.common.*), follow a
  // switch while the window stays open, and an explicit label given by the caller wins over that default.
  describe('accessible names of the shell controls (F06)', () => {
    const setLocale = (locale: string) => {
      ;(i18n.global.locale as unknown as { value: string }).value = locale
    }
    const control = (action: string) => document.querySelector<HTMLButtonElement>(`[data-modeless-action="${action}"]`)

    afterEach(() => setLocale('ko'))

    it.each([
      ['ko', '최소화', '복원', '닫기'],
      ['ja', '最小化', '元に戻す', '閉じる'],
      ['en', 'Minimize', 'Restore', 'Close'],
    ])('names minimize / restore / close in %s', async (locale, minimize, restore, close) => {
      registerAppMessages(i18n)
      setLocale(locale)
      const wrapper = mountPanel()
      expect(control('minimize')?.getAttribute('aria-label')).toBe(minimize)
      expect(control('close')?.getAttribute('aria-label')).toBe(close)
      control('minimize')!.click()
      await wrapper.vm.$nextTick()
      expect(document.querySelector('.ui-modeless-panel')?.classList.contains('is-minimized')).toBe(true)
      expect(control('restore')?.getAttribute('aria-label')).toBe(restore)
      wrapper.unmount()
    })

    it('follows a language switch while the window stays open, keeping its state and its icons', async () => {
      registerAppMessages(i18n)
      setLocale('ko')
      const wrapper = mountPanel()
      control('minimize')!.click()
      await wrapper.vm.$nextTick()
      setLocale('ja')
      await wrapper.vm.$nextTick()
      expect(control('restore')?.getAttribute('aria-label')).toBe('元に戻す')
      expect(control('close')?.getAttribute('aria-label')).toBe('閉じる')
      expect(document.querySelector('.ui-modeless-panel')?.classList.contains('is-minimized')).toBe(true)
      expect(control('restore')?.textContent?.trim()).toBe('□')
      expect(control('close')?.textContent?.trim()).toBe('×')
      control('close')!.click()
      expect(wrapper.emitted('close')).toHaveLength(1)
      wrapper.unmount()
    })

    it('an explicit label wins over the localized default', async () => {
      registerAppMessages(i18n)
      setLocale('ja')
      const wrapper = mountPanel({ minimizeLabel: 'Fold window', restoreLabel: 'Unfold window', closeLabel: 'Dismiss window' })
      expect(control('minimize')?.getAttribute('aria-label')).toBe('Fold window')
      expect(control('close')?.getAttribute('aria-label')).toBe('Dismiss window')
      control('minimize')!.click()
      await wrapper.vm.$nextTick()
      expect(control('restore')?.getAttribute('aria-label')).toBe('Unfold window')
      wrapper.unmount()
    })
  })
})
