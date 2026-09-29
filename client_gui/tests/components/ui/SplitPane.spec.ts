import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SplitPane from '@/components/ui/SplitPane.vue'

// jsdom does not implement ResizeObserver at all, so a minimal recording stub stands in
// for it: it records the elements it observes and lets a test invoke the callback
// itself to simulate a parent-driven resize that never fires a `window` `resize` event
// (flex/grid siblings changing, a sidebar collapsing -- rejection finding #2).
class MockResizeObserver {
  static instances: MockResizeObserver[] = []
  readonly callback: ResizeObserverCallback
  readonly observed: Element[] = []
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    MockResizeObserver.instances.push(this)
  }
  observe(el: Element): void {
    this.observed.push(el)
  }
  unobserve(): void {}
  disconnect(): void {}
  trigger(): void {
    this.callback([] as unknown as ResizeObserverEntry[], this as unknown as ResizeObserver)
  }
}
;(globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver = MockResizeObserver

// T0010 §5/§13.1: two-area split with a draggable/keyboard-adjustable boundary, min/max
// clamp, separator semantics, and listener cleanup.
describe('components/ui/SplitPane (T0010)', () => {
  function mountSplit(props: Record<string, unknown> = {}) {
    return mount(SplitPane, {
      props: { initialSize: 200, minSize: 80, secondMinSize: 80, ...props },
      slots: { first: '<div class="first">A</div>', second: '<div class="second">B</div>' },
      attachTo: document.body,
    })
  }

  it('renders both panes and a separator', () => {
    const wrapper = mountSplit()
    expect(wrapper.find('.first').exists()).toBe(true)
    expect(wrapper.find('.second').exists()).toBe(true)
    expect(wrapper.find('[role="separator"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('defaults to horizontal direction with a vertical splitter (row layout)', () => {
    const wrapper = mountSplit()
    expect(wrapper.find('.ui-splitpane').classes()).toContain('ui-splitpane--horizontal')
    expect(wrapper.find('[role="separator"]').attributes('aria-orientation')).toBe('vertical')
    wrapper.unmount()
  })

  it('supports vertical direction with a horizontal splitter (column layout)', () => {
    const wrapper = mountSplit({ direction: 'vertical' })
    expect(wrapper.find('.ui-splitpane').classes()).toContain('ui-splitpane--vertical')
    expect(wrapper.find('[role="separator"]').attributes('aria-orientation')).toBe('horizontal')
    wrapper.unmount()
  })

  it('sizes the first pane from initialSize', () => {
    const wrapper = mountSplit({ initialSize: 260 })
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement
    expect(first.style.flex).toBe('0 0 260px')
    wrapper.unmount()
  })

  it('drags the splitter to resize the first pane (horizontal)', async () => {
    const wrapper = mountSplit({ initialSize: 200 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 300, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 350, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(first.style.flex).toBe('0 0 250px')
    wrapper.unmount()
  })

  it('drags the splitter to resize the first pane (vertical)', async () => {
    const wrapper = mountSplit({ direction: 'vertical', initialSize: 200 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 300 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 0, clientY: 260 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(first.style.flex).toBe('0 0 160px')
    wrapper.unmount()
  })

  it('clamps at minSize when dragged far past the minimum', async () => {
    const wrapper = mountSplit({ initialSize: 200, minSize: 80 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 500, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: -1000, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(first.style.flex).toBe('0 0 80px')
    wrapper.unmount()
  })

  it('clamps at maxSize when dragged far past the maximum', async () => {
    const wrapper = mountSplit({ initialSize: 200, maxSize: 400 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 5000, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(first.style.flex).toBe('0 0 400px')
    wrapper.unmount()
  })

  it('clamps against the container so the second pane keeps secondMinSize', async () => {
    const wrapper = mountSplit({ initialSize: 200, secondMinSize: 100, maxSize: 900 })
    const container = wrapper.find('.ui-splitpane').element as HTMLElement
    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 500 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 5000, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    // container 500 - secondMinSize 100 - splitter thickness 6 = 394
    expect(first.style.flex).toBe('0 0 394px')
    wrapper.unmount()
  })

  it('emits resize during drag and resize-end once released', async () => {
    const wrapper = mountSplit({ initialSize: 200 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 20, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 40, clientY: 0 }))
    expect(wrapper.emitted('resize')).toHaveLength(2)
    expect(wrapper.emitted('update:size')).toHaveLength(2)
    expect(wrapper.emitted('resize-end')).toBeUndefined()

    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    expect(wrapper.emitted('resize-end')).toHaveLength(1)
    expect((wrapper.emitted('resize-end') as unknown[][])[0][0]).toBe(240)
    wrapper.unmount()
  })

  it('resizes via arrow keys on the separator and immediately fires resize-end', async () => {
    const wrapper = mountSplit({ initialSize: 200, step: 10 })
    const splitter = wrapper.find('[role="separator"]')

    await splitter.trigger('keydown', { key: 'ArrowRight' })
    expect((wrapper.emitted('resize') as unknown[][])[0][0]).toBe(210)
    expect((wrapper.emitted('resize-end') as unknown[][])[0][0]).toBe(210)

    await splitter.trigger('keydown', { key: 'ArrowLeft' })
    expect((wrapper.emitted('resize') as unknown[][])[1][0]).toBe(200)
    wrapper.unmount()
  })

  it('resizes via up/down arrow keys when vertical', async () => {
    const wrapper = mountSplit({ direction: 'vertical', initialSize: 200, step: 10 })
    const splitter = wrapper.find('[role="separator"]')

    await splitter.trigger('keydown', { key: 'ArrowDown' })
    expect((wrapper.emitted('resize') as unknown[][])[0][0]).toBe(210)
    wrapper.unmount()
  })

  it('exposes role=separator and aria-orientation for accessibility', () => {
    const wrapper = mountSplit()
    const separator = wrapper.find('[role="separator"]')
    expect(separator.attributes('aria-orientation')).toBeDefined()
    expect(separator.attributes('aria-valuenow')).toBe('200')
    expect(separator.attributes('aria-valuemin')).toBe('80')
    wrapper.unmount()
  })

  it('removes document listeners once a drag ends (no stray listeners after mouseup)', async () => {
    const wrapper = mountSplit({ initialSize: 200 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 20, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    const sizeAfterFirstDrag = first.style.flex

    // A further mousemove with no new mousedown must have no effect.
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 999, clientY: 0 }))
    await wrapper.vm.$nextTick()
    expect(first.style.flex).toBe(sizeAfterFirstDrag)
    wrapper.unmount()
  })

  it('does not throw and stops reacting to window resize after unmount', () => {
    const wrapper = mountSplit()
    wrapper.unmount()
    expect(() => window.dispatchEvent(new Event('resize'))).not.toThrow()
  })

  it('observes its own container with ResizeObserver on mount and disconnects on unmount', () => {
    MockResizeObserver.instances.length = 0
    const wrapper = mountSplit()
    const container = wrapper.find('.ui-splitpane').element as HTMLElement
    expect(MockResizeObserver.instances).toHaveLength(1)
    expect(MockResizeObserver.instances[0].observed).toContain(container)
    wrapper.unmount()
  })

  it('reclamps the first pane when the parent shrinks without any window resize event', async () => {
    MockResizeObserver.instances.length = 0
    const wrapper = mountSplit({ initialSize: 300, secondMinSize: 100, maxSize: 900 })
    const container = wrapper.find('.ui-splitpane').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    // A flex/grid/sidebar-driven parent resize: the container's own clientWidth
    // changes but `window` never fires `resize`.
    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 320 })
    MockResizeObserver.instances[0].trigger()
    await wrapper.vm.$nextTick()

    // 320 - secondMinSize 100 - splitter 6 = 214, below the current firstSize (300).
    expect(first.style.flex).toBe('0 0 214px')
    wrapper.unmount()
  })

  it('updates aria-valuemax reactively when the container resizes via ResizeObserver', async () => {
    MockResizeObserver.instances.length = 0
    const wrapper = mountSplit({ initialSize: 200, secondMinSize: 100, maxSize: 900 })
    const container = wrapper.find('.ui-splitpane').element as HTMLElement
    const separator = wrapper.find('[role="separator"]')

    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 500 })
    MockResizeObserver.instances[0].trigger()
    await wrapper.vm.$nextTick()

    // 500 - 100 - 6 = 394 -- stale before this fix, since `effectiveMaxSize` had no
    // reactive dependency on the container's measured size.
    expect(separator.attributes('aria-valuemax')).toBe('394')
    wrapper.unmount()
  })

  it('updates aria-valuemax and the End-key bound after a window resize shrinks the container', async () => {
    const wrapper = mountSplit({ initialSize: 200, secondMinSize: 100, maxSize: 900, step: 10 })
    const container = wrapper.find('.ui-splitpane').element as HTMLElement
    const separator = wrapper.find('[role="separator"]')

    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 260 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()

    // 260 - 100 - 6 = 154
    expect(separator.attributes('aria-valuemax')).toBe('154')

    await separator.trigger('keydown', { key: 'End' })
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement
    expect(first.style.flex).toBe('0 0 154px')
    wrapper.unmount()
  })

  it('ignores a non-primary-button mousedown on the splitter (no drag on right/middle click)', async () => {
    const wrapper = mountSplit({ initialSize: 200 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 500, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(first.style.flex).toBe('0 0 200px')

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 1, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 500, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(first.style.flex).toBe('0 0 200px')
    wrapper.unmount()
  })

  it('suppresses text selection during a primary-button drag and restores it after', async () => {
    const wrapper = mountSplit({ initialSize: 200 })
    const splitter = wrapper.find('.ui-splitpane-splitter').element as HTMLElement
    document.body.style.userSelect = ''

    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 0, clientY: 0 }))
    expect(document.body.style.userSelect).toBe('none')

    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    expect(document.body.style.userSelect).toBe('')
    wrapper.unmount()
  })

  // rev2: `effectiveMaxSize` is a computed cached on `containerResizeTick`. Before this
  // fix, `onMounted()` called a bare `reclamp()` that never bumped that tick, so even
  // though `containerRef` became measurable right after mount, the computed kept serving
  // its pre-mount value (extent 0 -> `minSize`, since the default `maxSize` is Infinity)
  // until the first external `window resize`/ResizeObserver callback.
  it('reflects the real container width in aria-valuemax/End immediately after mount, before any resize event', async () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 500 })
    try {
      const wrapper = mountSplit({ initialSize: 200, minSize: 80, secondMinSize: 100, maxSize: 900, step: 10 })
      // The mount-time measurement bumps `containerResizeTick`, which invalidates the
      // `effectiveMaxSize` computed immediately -- but patching that new value into the
      // DOM is still a normal (batched) Vue re-render, so the attribute only reflects it
      // after a tick.
      await wrapper.vm.$nextTick()
      const separator = wrapper.find('[role="separator"]')
      // 500 - secondMinSize 100 - splitter 6 = 394, available without a window resize or
      // a ResizeObserver trigger -- only the mount-time measurement.
      expect(separator.attributes('aria-valuemax')).toBe('394')

      await separator.trigger('keydown', { key: 'End' })
      const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement
      expect(first.style.flex).toBe('0 0 394px')
      wrapper.unmount()
    } finally {
      if (originalDescriptor) Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalDescriptor)
    }
  })

  it('reflects the real container width on mount even when ResizeObserver is unavailable', async () => {
    const OriginalResizeObserver = (globalThis as unknown as { ResizeObserver?: typeof MockResizeObserver }).ResizeObserver
    delete (globalThis as unknown as { ResizeObserver?: typeof MockResizeObserver }).ResizeObserver
    const originalDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 500 })
    try {
      const wrapper = mountSplit({ initialSize: 200, minSize: 80, secondMinSize: 100, maxSize: 900 })
      await wrapper.vm.$nextTick()
      const separator = wrapper.find('[role="separator"]')
      expect(separator.attributes('aria-valuemax')).toBe('394')
      wrapper.unmount()
    } finally {
      if (originalDescriptor) Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalDescriptor)
      ;(globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver = OriginalResizeObserver as typeof MockResizeObserver
    }
  })
  it('keeps minSize authoritative when maxSize is smaller for initial, drag, Home, and End', async () => {
    MockResizeObserver.instances.length = 0
    const wrapper = mountSplit({ initialSize: 20, minSize: 100, maxSize: 50, secondMinSize: 80 })
    const container = wrapper.find('.ui-splitpane').element as HTMLElement
    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 500 })
    MockResizeObserver.instances[0].trigger()
    await wrapper.vm.$nextTick()

    const separator = wrapper.find('[role="separator"]')
    const first = wrapper.find('.ui-splitpane-pane--first').element as HTMLElement
    expect(Number(separator.attributes('aria-valuemax'))).toBeGreaterThanOrEqual(
      Number(separator.attributes('aria-valuemin')),
    )
    expect(first.style.flex).toBe('0 0 100px')

    const splitter = separator.element as HTMLElement
    splitter.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 100 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: -1000 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(first.style.flex).toBe('0 0 100px')

    await separator.trigger('keydown', { key: 'Home' })
    expect(first.style.flex).toBe('0 0 100px')
    await separator.trigger('keydown', { key: 'End' })
    expect(first.style.flex).toBe('0 0 100px')
    wrapper.unmount()
  })
})
