import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import ResizableSidebar from '@main/components/ResizableSidebar.vue'
import { useLayoutStore } from '@main/stores/layout'

// 0255 R0001 / NR0004 §4 — the defect these cover is that the INNER explorer hid its
// body while the OUTER frame kept its flex space. So the assertions here are on the
// frame's inline flex, not on the is-collapsed class: the class is only indirect
// evidence via CSS, and would still pass if the frame handed back the wrong height.
const COLLAPSED_FLEX = 'flex: 0 0 35px'

function mountSidebar() {
  return mount(ResizableSidebar, {
    slots: {
      top: '<div data-test="top-slot">files</div>',
      bottom: '<div data-test="bottom-slot">documents</div>',
    },
  })
}

describe('ResizableSidebar frame accordion (0255 R0001)', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('starts with the file frame folded and document frame filling the remainder', () => {
    const wrapper = mountSidebar()
    expect(wrapper.get('[data-test="file-explorer-frame"]').classes()).toContain('is-collapsed')
    expect(wrapper.get('[data-test="document-explorer-frame"]').classes()).not.toContain('is-collapsed')
    expect(wrapper.find('[data-test="explorer-frame-resizer"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="file-explorer-frame"]').attributes('style')).toContain(COLLAPSED_FLEX)
    expect(wrapper.get('[data-test="document-explorer-frame"]').attributes('style')).toContain('flex: 1 1 auto')
  })

  // NR0004 §7.12 — a folded frame must hand its vertical space back, not merely hide
  // its body. Asserted on each frame in each folded combination.
  it('gives the folded frame exactly the 35px header height back', async () => {
    const wrapper = mountSidebar()
    const store = useLayoutStore()
    const file = () => wrapper.get('[data-test="file-explorer-frame"]')
    const documents = () => wrapper.get('[data-test="document-explorer-frame"]')

    // File folded / document expanded (the default).
    expect(file().attributes('style')).toContain(COLLAPSED_FLEX)

    // File expanded / document folded.
    store.setFileExplorerCollapsed(false)
    store.setDocumentExplorerCollapsed(true)
    await nextTick()
    expect(documents().attributes('style')).toContain(COLLAPSED_FLEX)
    expect(file().attributes('style')).toContain('flex: 1 1 auto')

    // Both folded — both frames are 35px and nothing else takes space.
    store.setFileExplorerCollapsed(true)
    await nextTick()
    expect(file().attributes('style')).toContain(COLLAPSED_FLEX)
    expect(documents().attributes('style')).toContain(COLLAPSED_FLEX)
    expect(wrapper.find('[data-test="explorer-frame-resizer"]').exists()).toBe(false)
  })

  it('supports all four states without coupling the two frames', async () => {
    const wrapper = mountSidebar()
    const store = useLayoutStore()
    const file = () => wrapper.get('[data-test="file-explorer-frame"]')
    const documents = () => wrapper.get('[data-test="document-explorer-frame"]')

    store.toggleFileExplorer() // both expanded
    await nextTick()
    expect(file().classes()).not.toContain('is-collapsed')
    expect(documents().classes()).not.toContain('is-collapsed')
    expect(wrapper.find('[data-test="explorer-frame-resizer"]').exists()).toBe(true)

    store.toggleDocumentExplorer() // files only
    await nextTick()
    expect(file().classes()).not.toContain('is-collapsed')
    expect(documents().classes()).toContain('is-collapsed')
    expect(file().attributes('style')).toContain('flex: 1 1 auto')
    expect(documents().attributes('style')).toContain(COLLAPSED_FLEX)

    store.toggleFileExplorer() // both folded
    await nextTick()
    expect(file().classes()).toContain('is-collapsed')
    expect(documents().classes()).toContain('is-collapsed')
    expect(wrapper.find('[data-test="explorer-frame-resizer"]').exists()).toBe(false)

    store.toggleDocumentExplorer() // documents only
    await nextTick()
    expect(file().classes()).toContain('is-collapsed')
    expect(documents().classes()).not.toContain('is-collapsed')
    expect(file().attributes('style')).toContain(COLLAPSED_FLEX)
    expect(documents().attributes('style')).toContain('flex: 1 1 auto')
  })

  // NR0004 §7.6 — the ratio must survive a fold round-trip as RENDERED GEOMETRY.
  // Asserting store.fileExplorerRatio instead would pass trivially: no fold path
  // ever writes that value, so the assertion could not fail.
  it('restores the pre-fold split geometry after both frames re-expand', async () => {
    const wrapper = mountSidebar()
    const store = useLayoutStore()
    const file = () => wrapper.get('[data-test="file-explorer-frame"]')
    const documents = () => wrapper.get('[data-test="document-explorer-frame"]')

    store.setFileExplorerCollapsed(false)
    store.setFileExplorerRatio(0.3)
    await nextTick()
    expect(file().attributes('style')).toContain('flex: 3')
    expect(documents().attributes('style')).toContain('flex: 7')
    expect(wrapper.find('[data-test="explorer-frame-resizer"]').exists()).toBe(true)

    store.setDocumentExplorerCollapsed(true)
    await nextTick()
    expect(documents().attributes('style')).toContain(COLLAPSED_FLEX)
    expect(wrapper.find('[data-test="explorer-frame-resizer"]').exists()).toBe(false)

    store.setDocumentExplorerCollapsed(false)
    await nextTick()
    expect(file().attributes('style')).toContain('flex: 3')
    expect(documents().attributes('style')).toContain('flex: 7')
    expect(wrapper.find('[data-test="explorer-frame-resizer"]').exists()).toBe(true)
  })

  // NR0004 §7.10 — the overlay sidebar's open/close is a separate axis from the two
  // frame accordions. The 1023px breakpoint itself is a media query (not evaluated in
  // jsdom); what is asserted here is the state non-conflict the breakpoint relies on.
  it('keeps frame state independent of the mobile overlay open/close', async () => {
    const wrapper = mountSidebar()
    const store = useLayoutStore()
    store.setFileExplorerCollapsed(false)
    store.setDocumentExplorerCollapsed(true)
    await nextTick()

    store.toggleSidebar() // open the overlay
    await nextTick()
    expect(store.sidebarOpen).toBe(true)
    expect(wrapper.get('.app-sidebar').classes()).toContain('open')
    expect(store.fileExplorerCollapsed).toBe(false)
    expect(store.documentExplorerCollapsed).toBe(true)
    expect(wrapper.get('[data-test="document-explorer-frame"]').attributes('style')).toContain(COLLAPSED_FLEX)

    store.toggleFileExplorer() // folding a frame must not close the overlay
    await nextTick()
    expect(store.sidebarOpen).toBe(true)

    store.toggleSidebar() // close the overlay — frame states survive
    await nextTick()
    expect(store.sidebarOpen).toBe(false)
    expect(store.fileExplorerCollapsed).toBe(true)
    expect(store.documentExplorerCollapsed).toBe(true)
  })
})
