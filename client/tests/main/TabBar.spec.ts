import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import TabBar from '@main/components/TabBar.vue'
import { useTabsStore, type Tab } from '@main/stores/tabs'

class ResizeObserverMock {
  observe() {}
  disconnect() {}
}

function makeTab(id: string): Tab {
  return {
    id,
    title: `Tab ${id}`,
    path: `/docs/${id}.md`,
    type: 'md',
  }
}

function setDimension(element: Element, property: string, value: number) {
  Object.defineProperty(element, property, { configurable: true, value })
}

async function flushTabTracking() {
  await nextTick()
  await nextTick()
  await nextTick()
}

function mountTabBar() {
  return mount(TabBar, {
    global: {
      plugins: [i18n],
      stubs: {
        ContextMenu: true,
        ContextMenuItem: true,
      },
    },
  })
}

function mockLayout(
  wrapper: ReturnType<typeof mountTabBar>,
  positions: Record<string, { left: number; width: number }>,
  options: {
    clientWidth?: number
    scrollLeft?: number
    scrollWidth?: number
    scrollButtonsWidth?: number
  } = {},
) {
  const bar = wrapper.get('.tabs-bar').element as HTMLElement
  const fullWidth = options.clientWidth ?? 300
  const scrollButtonsWidth = options.scrollButtonsWidth ?? 64
  Object.defineProperty(bar, 'clientWidth', {
    configurable: true,
    get: () => fullWidth - (wrapper.find('.tabs-scroll-left').exists() ? scrollButtonsWidth : 0),
  })
  setDimension(bar, 'scrollWidth', options.scrollWidth ?? 900)
  bar.scrollLeft = options.scrollLeft ?? 0
  bar.getBoundingClientRect = vi.fn(() => ({
    x: 100,
    y: 0,
    left: 100,
    top: 0,
    right: 100 + bar.clientWidth,
    bottom: 38,
    width: bar.clientWidth,
    height: 38,
    toJSON: () => ({}),
  }))

  for (const [id, position] of Object.entries(positions)) {
    const tab = wrapper.findAll('.tab-item').find(item => item.element.id === id)?.element
    if (!tab) throw new Error(`Missing tab item: ${id}`)
    setDimension(tab, 'offsetLeft', position.left)
    setDimension(tab, 'offsetWidth', position.width)
    tab.getBoundingClientRect = vi.fn(() => {
      const left = 100 + position.left - bar.scrollLeft
      return {
        x: left,
        y: 0,
        left,
        top: 0,
        right: left + position.width,
        bottom: 36,
        width: position.width,
        height: 36,
        toJSON: () => ({}),
      }
    })
  }

  const scrollTo = vi.fn(({ left }: ScrollToOptions) => {
    bar.scrollLeft = left ?? bar.scrollLeft
  })
  bar.scrollTo = scrollTo
  return { bar, scrollTo }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
  i18n.global.locale.value = 'en'
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
})

describe('TabBar active tab tracking', () => {
  it('reveals a newly opened tab clipped on the right', async () => {
    const store = useTabsStore()
    store.tabs = [makeTab('a'), makeTab('b')]
    store.activeTabId = 'a'
    const wrapper = mountTabBar()
    const { scrollTo } = mockLayout(wrapper, {
      'tab-overview': { left: 0, width: 80 },
      'tab-a': { left: 80, width: 120 },
      'tab-b': { left: 360, width: 120 },
    })

    store.openTab(makeTab('b'))
    await flushTabTracking()

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 244, behavior: 'smooth' })
    expect(wrapper.get('.tabs-scroll-left').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('.tabs-scroll-right').attributes('disabled')).toBeDefined()
  })

  it('remeasures after overflow buttons expose half of the active tab', async () => {
    const store = useTabsStore()
    store.tabs = [makeTab('a')]
    store.activeTabId = null
    const wrapper = mountTabBar()
    const { scrollTo } = mockLayout(wrapper, {
      'tab-overview': { left: 0, width: 80 },
      'tab-a': { left: 160, width: 120 },
    })

    store.openTab(makeTab('a'))
    await flushTabTracking()

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 44, behavior: 'smooth' })
  })

  it('reveals an existing tab clipped on the left', async () => {
    const store = useTabsStore()
    store.tabs = [makeTab('a'), makeTab('b')]
    store.activeTabId = 'b'
    const wrapper = mountTabBar()
    const { scrollTo } = mockLayout(wrapper, {
      'tab-overview': { left: 0, width: 80 },
      'tab-a': { left: 80, width: 120 },
      'tab-b': { left: 360, width: 120 },
    }, { scrollLeft: 250 })

    store.openTab(makeTab('a'))
    await flushTabTracking()

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 80, behavior: 'smooth' })
  })

  it('does not move when the active tab is fully visible', async () => {
    const store = useTabsStore()
    store.tabs = [makeTab('a')]
    store.activeTabId = null
    const wrapper = mountTabBar()
    const { scrollTo } = mockLayout(wrapper, {
      'tab-overview': { left: 0, width: 80 },
      'tab-a': { left: 100, width: 120 },
    })

    store.openTab(makeTab('a'))
    await flushTabTracking()

    expect(scrollTo).not.toHaveBeenCalled()
  })

  it('tracks the restored active tab on mount', async () => {
    localStorage.setItem('flowgate.user.guest.tabs', JSON.stringify({
      tabs: [makeTab('a'), makeTab('restored')],
      activeTabId: 'restored',
    }))
    const store = useTabsStore()
    expect(store.activeTabId).toBe('restored')
    const wrapper = mountTabBar()
    const { scrollTo } = mockLayout(wrapper, {
      'tab-overview': { left: 0, width: 80 },
      'tab-a': { left: 80, width: 120 },
      'tab-restored': { left: 500, width: 120 },
    })

    await flushTabTracking()

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 384, behavior: 'smooth' })
  })

  it('tracks the adjacent tab selected after closing the active tab', async () => {
    const store = useTabsStore()
    store.tabs = [makeTab('a'), makeTab('b'), makeTab('c')]
    store.activeTabId = 'c'
    const wrapper = mountTabBar()
    const { scrollTo } = mockLayout(wrapper, {
      'tab-overview': { left: 0, width: 80 },
      'tab-a': { left: 80, width: 120 },
      'tab-b': { left: 340, width: 120 },
      'tab-c': { left: 500, width: 120 },
    })

    store.closeTab('c')
    await flushTabTracking()

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 224, behavior: 'smooth' })
  })

  it('tracks reopening the same active document', async () => {
    const docId = 'flowgate.default.0034.0001-R'
    const store = useTabsStore()
    store.tabs = [makeTab(docId)]
    store.activeTabId = docId
    const wrapper = mountTabBar()
    const { scrollTo } = mockLayout(wrapper, {
      'tab-overview': { left: 0, width: 80 },
      [`tab-${docId}`]: { left: 420, width: 120 },
    })

    store.openTab(makeTab(docId))
    await flushTabTracking()

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 304, behavior: 'smooth' })
  })
})
