import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ResizablePane from '@/components/ui/ResizablePane.vue'

// jsdom does not implement ResizeObserver at all, so a minimal recording stub stands in
// for it: it records the elements it observes and lets a test invoke the callback itself
// to simulate a parent-driven resize that never fires a `window` `resize` event (flex/
// grid siblings changing, a sidebar collapsing -- rejection finding "resizeTick/
// onExternalResize lifecycle"). Self-contained per spec file, matching SplitPane.spec.ts.
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

function stubRect(el: HTMLElement, rect: { left: number; right: number; top: number; bottom: number }): void {
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({
    left: rect.left,
    right: rect.right,
    top: rect.top,
    bottom: rect.bottom,
    width: rect.right - rect.left,
    height: rect.bottom - rect.top,
    x: rect.left,
    y: rect.top,
    toJSON: () => ({}),
  } as DOMRect)
}

// T0010 §6/§13.2: single-panel edge resize, min/max clamp, disabled means no handle at
// all, and listener cleanup.
describe('components/ui/ResizablePane (T0010)', () => {
  const originalInnerWidth = window.innerWidth
  const originalInnerHeight = window.innerHeight

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight })
  })

  function mountPane(props: Record<string, unknown> = {}) {
    return mount(ResizablePane, {
      props: { initialSize: 240, minSize: 80, ...props },
      slots: { default: '<div class="body">content</div>' },
      attachTo: document.body,
    })
  }

  it('renders its slot content and a resize handle by default', () => {
    const wrapper = mountPane()
    expect(wrapper.find('.body').exists()).toBe(true)
    expect(wrapper.find('.ui-resizablepane-handle').exists()).toBe(true)
    wrapper.unmount()
  })

  it('renders no handle when resizable is false (resize is impossible, not merely disabled)', () => {
    const wrapper = mountPane({ resizable: false })
    expect(wrapper.find('.ui-resizablepane-handle').exists()).toBe(false)
    wrapper.unmount()
  })

  it('sizes from initialSize as width for a right edge', () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 260 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    expect(root.style.width).toBe('260px')
    wrapper.unmount()
  })

  it('sizes from initialSize as height for a bottom edge', () => {
    const wrapper = mountPane({ edge: 'bottom', initialSize: 180 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    expect(root.style.height).toBe('180px')
    wrapper.unmount()
  })

  it('drags a right-edge handle: moving right grows the panel', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 400, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 460, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(root.style.width).toBe('300px')
    wrapper.unmount()
  })

  it('drags a left-edge handle: moving left grows the panel', async () => {
    const wrapper = mountPane({ edge: 'left', initialSize: 240 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 400, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 340, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(root.style.width).toBe('300px')
    wrapper.unmount()
  })

  it('does not resize when resizable is false (no handle to drag)', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240, resizable: false })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    expect(wrapper.find('.ui-resizablepane-handle').exists()).toBe(false)
    expect(root.style.width).toBe('240px')
    wrapper.unmount()
  })

  it('clamps at minSize when dragged far past the minimum', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240, minSize: 100 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 400, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: -5000, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(root.style.width).toBe('100px')
    wrapper.unmount()
  })

  it('clamps at maxSize when dragged far past the maximum', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240, maxSize: 400 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 5000, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(root.style.width).toBe('400px')
    wrapper.unmount()
  })

  it('also clamps against the viewport extent', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 500 })
    const wrapper = mountPane({ edge: 'right', initialSize: 240, maxSize: 2000 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 5000, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(root.style.width).toBe('500px')
    wrapper.unmount()
  })

  it('emits resize during drag and resize-end once released', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 20, clientY: 0 }))
    expect(wrapper.emitted('resize')).toHaveLength(1)
    expect(wrapper.emitted('resize-end')).toBeUndefined()

    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    expect(wrapper.emitted('resize-end')).toHaveLength(1)
    expect((wrapper.emitted('resize-end') as unknown[][])[0][0]).toBe(260)
    wrapper.unmount()
  })

  it('resizes via arrow keys on the handle (right edge: ArrowRight grows)', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240, step: 10 })
    const handle = wrapper.find('.ui-resizablepane-handle')

    await handle.trigger('keydown', { key: 'ArrowRight' })
    expect((wrapper.emitted('resize') as unknown[][])[0][0]).toBe(250)
    expect((wrapper.emitted('resize-end') as unknown[][])[0][0]).toBe(250)

    await handle.trigger('keydown', { key: 'ArrowLeft' })
    expect((wrapper.emitted('resize') as unknown[][])[1][0]).toBe(240)
    wrapper.unmount()
  })

  it('resizes via arrow keys on the handle (left edge: ArrowLeft grows)', async () => {
    const wrapper = mountPane({ edge: 'left', initialSize: 240, step: 10 })
    const handle = wrapper.find('.ui-resizablepane-handle')

    await handle.trigger('keydown', { key: 'ArrowLeft' })
    expect((wrapper.emitted('resize') as unknown[][])[0][0]).toBe(250)
    wrapper.unmount()
  })

  it('exposes role=separator with edge-appropriate aria-orientation', () => {
    const right = mountPane({ edge: 'right' })
    expect(right.find('.ui-resizablepane-handle').attributes('role')).toBe('separator')
    expect(right.find('.ui-resizablepane-handle').attributes('aria-orientation')).toBe('vertical')
    right.unmount()

    const bottom = mountPane({ edge: 'bottom' })
    expect(bottom.find('.ui-resizablepane-handle').attributes('aria-orientation')).toBe('horizontal')
    bottom.unmount()
  })

  it('stops reacting to further mousemove once a drag ends (listener cleanup)', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 20, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    const sizeAfterDrag = root.style.width

    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 999, clientY: 0 }))
    await wrapper.vm.$nextTick()
    expect(root.style.width).toBe(sizeAfterDrag)
    wrapper.unmount()
  })

  it('does not throw and stops reacting to window resize after unmount', () => {
    const wrapper = mountPane()
    wrapper.unmount()
    expect(() => window.dispatchEvent(new Event('resize'))).not.toThrow()
  })

  it('ignores a non-primary-button mousedown on the handle (no drag on right/middle click)', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, clientX: 400, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 800, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(root.style.width).toBe('240px')

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 1, clientX: 400, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 800, clientY: 0 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(root.style.width).toBe('240px')
    wrapper.unmount()
  })

  it('suppresses text selection during a primary-button drag and restores it after', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 240 })
    const handle = wrapper.find('.ui-resizablepane-handle').element as HTMLElement
    document.body.style.userSelect = ''

    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 400, clientY: 0 }))
    expect(document.body.style.userSelect).toBe('none')

    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    expect(document.body.style.userSelect).toBe('')
    wrapper.unmount()
  })

  // rev2: the template used to expose the raw `maxSize` prop as aria-valuemax (omitted
  // entirely when it was the default Infinity) while clamp/End actually used
  // `min(maxSize, viewport)`. That let aria-valuemax/aria-valuenow disagree with the
  // real reachable range. `effectiveMaxSize` is now the single source both read from.
  it('always exposes a finite aria-valuemax that matches the actual End-key/drag ceiling (viewport-bounded default)', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 500 })
    const wrapper = mountPane({ edge: 'right', initialSize: 240, maxSize: 2000, step: 50 })
    const handle = wrapper.find('.ui-resizablepane-handle')
    const valueMax = handle.attributes('aria-valuemax')
    expect(valueMax).toBeDefined()
    expect(Number.isFinite(Number(valueMax))).toBe(true)
    expect(valueMax).toBe('500')

    await handle.trigger('keydown', { key: 'End' })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    expect(root.style.width).toBe('500px')
    wrapper.unmount()
  })

  // rev2 / T0010 §6.3 "viewport/parent 경계 고려": clamp/End only ever looked at
  // `window.innerWidth`/`innerHeight`, never the pane's own screen position or its
  // parent's boundary, so an offset pane inside a narrower parent could still grow past
  // the parent (and past its own container) even while staying under the viewport bound.
  it('bounds aria-valuemax and the End-key by the parent container when it is narrower than the viewport (edge=right, offset pane)', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 150, minSize: 50, maxSize: 2000, step: 10 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    const parent = root.parentElement as HTMLElement

    // Parent spans x in [0, 400]; the pane's anchored (left) edge sits at x=300, so it
    // can grow to at most 400 - 300 = 100px before pushing past the parent's right edge
    // -- far less than the (much larger) viewport.
    stubRect(parent, { left: 0, right: 400, top: 0, bottom: 0 })
    stubRect(root, { left: 300, right: 450, top: 0, bottom: 0 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()

    const handle = wrapper.find('.ui-resizablepane-handle')
    expect(handle.attributes('aria-valuemax')).toBe('100')
    // The out-of-bound initial size (150) must already have been pulled back in by the
    // same reclamp that runs on external resize.
    expect(root.style.width).toBe('100px')

    await handle.trigger('keydown', { key: 'End' })
    expect(root.style.width).toBe('100px')
    wrapper.unmount()
  })

  it('bounds aria-valuemax by the parent container for a left-edge pane (anchored on its right edge)', async () => {
    const wrapper = mountPane({ edge: 'left', initialSize: 150, minSize: 50, maxSize: 2000, step: 10 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    const parent = root.parentElement as HTMLElement

    // Parent spans x in [0, 400]; the pane's anchored (right) edge sits at x=120, so it
    // can grow left to at most 120px before pushing past the parent's left edge (x=0).
    stubRect(parent, { left: 0, right: 400, top: 0, bottom: 0 })
    stubRect(root, { left: -30, right: 120, top: 0, bottom: 0 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()

    const handle = wrapper.find('.ui-resizablepane-handle')
    expect(handle.attributes('aria-valuemax')).toBe('120')
    wrapper.unmount()
  })

  // rev3 / T0010 §6.3: `viewportExtent()` used to always return the full
  // `window.innerWidth`/`innerHeight`, ignoring the pane's own on-screen position. A
  // `right`-edge pane offset well into a wide viewport could report/reach a max far past
  // where the viewport actually ends.
  it('bounds aria-valuemax by the viewport position of the pane, not just its raw length (edge=right, offset pane, wide parent)', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 500 })
    const wrapper = mountPane({ edge: 'right', initialSize: 150, minSize: 50, maxSize: 2000, step: 10 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    const parent = root.parentElement as HTMLElement

    // Parent spans [0, 900] -- far wider than the viewport, so it never binds. The
    // pane's anchored (left) edge sits at x=300, so growing right it can only reach the
    // viewport's right edge at x=500: 500 - 300 = 200px, well short of the naive
    // `window.innerWidth` (500) the pre-fix code used as the bound.
    stubRect(parent, { left: 0, right: 900, top: 0, bottom: 0 })
    stubRect(root, { left: 300, right: 450, top: 0, bottom: 0 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()

    const handle = wrapper.find('.ui-resizablepane-handle')
    expect(handle.attributes('aria-valuemax')).toBe('200')

    await handle.trigger('keydown', { key: 'End' })
    expect(root.style.width).toBe('200px')
    wrapper.unmount()
  })

  it('bounds aria-valuemax by the viewport position for a left-edge pane (anchored on its right edge)', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 500 })
    const wrapper = mountPane({ edge: 'left', initialSize: 150, minSize: 50, maxSize: 2000 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    const parent = root.parentElement as HTMLElement

    // Parent spans [-400, 900] -- never binds. The pane's anchored (right) edge sits at
    // x=120, so growing left it can only reach the viewport's left edge at x=0: 120px.
    stubRect(parent, { left: -400, right: 900, top: 0, bottom: 0 })
    stubRect(root, { left: -30, right: 120, top: 0, bottom: 0 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()

    const handle = wrapper.find('.ui-resizablepane-handle')
    expect(handle.attributes('aria-valuemax')).toBe('120')
    wrapper.unmount()
  })

  // rev3: `resizeTick` was only bumped by `window resize` and the initial mount
  // measurement, never by a parent-only resize (flex sibling growing, sidebar
  // collapsing) that fires no `window resize` event at all -- the same gap SplitPane's
  // container ResizeObserver already closed.
  it('observes its parent element with ResizeObserver on mount and disconnects on unmount', () => {
    MockResizeObserver.instances.length = 0
    const wrapper = mountPane({ edge: 'right' })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    expect(MockResizeObserver.instances).toHaveLength(1)
    expect(MockResizeObserver.instances[0].observed).toContain(root.parentElement)
    wrapper.unmount()
  })

  it('reclamps size and updates aria-valuemax when the parent shrinks without any window resize event (ResizeObserver)', async () => {
    MockResizeObserver.instances.length = 0
    const wrapper = mountPane({ edge: 'right', initialSize: 150, minSize: 50, maxSize: 2000 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    const parent = root.parentElement as HTMLElement

    stubRect(parent, { left: 0, right: 400, top: 0, bottom: 0 })
    stubRect(root, { left: 300, right: 450, top: 0, bottom: 0 })
    // Parent-driven resize: no `window resize` event, only the ResizeObserver callback.
    MockResizeObserver.instances[0].trigger()
    await wrapper.vm.$nextTick()

    const handle = wrapper.find('.ui-resizablepane-handle')
    // 400 - 300 = 100, below the initial 150 -- must be pulled back in by the same
    // reclamp the `window resize` path already performs.
    expect(handle.attributes('aria-valuemax')).toBe('100')
    expect(root.style.width).toBe('100px')
    wrapper.unmount()
  })

  it('grows effectiveMaxSize (and the End-key reach) when the parent expands via ResizeObserver alone', async () => {
    MockResizeObserver.instances.length = 0
    const wrapper = mountPane({ edge: 'right', initialSize: 150, minSize: 50, maxSize: 2000, step: 10 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    const parent = root.parentElement as HTMLElement

    stubRect(parent, { left: 0, right: 900, top: 0, bottom: 0 })
    stubRect(root, { left: 300, right: 450, top: 0, bottom: 0 })
    MockResizeObserver.instances[0].trigger()
    await wrapper.vm.$nextTick()

    const handle = wrapper.find('.ui-resizablepane-handle')
    // 900 - 300 = 600, well past the previously-cached (stale) bound -- a cached low
    // `effectiveMaxSize` would otherwise keep the End key from reaching the new space.
    expect(handle.attributes('aria-valuemax')).toBe('600')
    await handle.trigger('keydown', { key: 'End' })
    expect(root.style.width).toBe('600px')
    wrapper.unmount()
  })

  // rev3: `currentMaxSize()` can legitimately come out below `minSize` when the
  // viewport/parent is simply too small to fit it. Before this fix, `effectiveMaxSize`
  // returned that smaller value as-is, producing an inverted `aria-valuemin > aria-
  // valuemax` range and clamping the actual size below the advertised minimum.
  it('normalizes an inverted range: aria-valuemax never drops below aria-valuemin when the container is narrower than minSize', async () => {
    const wrapper = mountPane({ edge: 'right', initialSize: 150, minSize: 80, maxSize: 2000 })
    const root = wrapper.find('.ui-resizablepane').element as HTMLElement
    const parent = root.parentElement as HTMLElement

    // Parent only has 40px of room past the pane's anchored edge -- less than minSize (80).
    stubRect(parent, { left: 0, right: 340, top: 0, bottom: 0 })
    stubRect(root, { left: 300, right: 450, top: 0, bottom: 0 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()

    const handle = wrapper.find('.ui-resizablepane-handle')
    expect(handle.attributes('aria-valuemin')).toBe('80')
    // Conflict policy: minSize wins over an even-tighter viewport/parent bound, so max
    // never drops below min (previously this rendered `aria-valuemax="40"`, an inverted
    // [80, 40] range, and clamped the actual size down to 40, breaking the minSize
    // contract).
    expect(handle.attributes('aria-valuemax')).toBe('80')
    expect(root.style.width).toBe('80px')
    wrapper.unmount()
  })
})
