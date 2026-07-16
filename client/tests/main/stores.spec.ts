import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useLayoutStore } from '@main/stores/layout'
import { useTabsStore } from '@main/stores/tabs'
import { useExplorerStore } from '@main/stores/explorer'
import { useErrorStore } from '@main/stores/error'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
})

// ─── useLayoutStore ────────────────────────────────────────────────────────────

describe('useLayoutStore', () => {
  it('setSidebarWidth clamps to min 180', () => {
    const store = useLayoutStore()
    store.setSidebarWidth(50)
    expect(store.sidebarWidth).toBe(180)
  })

  it('setSidebarWidth clamps to max 480', () => {
    const store = useLayoutStore()
    store.setSidebarWidth(9999)
    expect(store.sidebarWidth).toBe(480)
  })

  it('setSidebarWidth accepts valid values', () => {
    const store = useLayoutStore()
    store.setSidebarWidth(320)
    expect(store.sidebarWidth).toBe(320)
  })

  it('setFileExplorerRatio clamps to min 0.15', () => {
    const store = useLayoutStore()
    store.setFileExplorerRatio(0)
    expect(store.fileExplorerRatio).toBe(0.15)
  })

  it('setFileExplorerRatio clamps to max 0.85', () => {
    const store = useLayoutStore()
    store.setFileExplorerRatio(1)
    expect(store.fileExplorerRatio).toBe(0.85)
  })

  it('toggleSidebar flips sidebarOpen', () => {
    const store = useLayoutStore()
    expect(store.sidebarOpen).toBe(false)
    store.toggleSidebar()
    expect(store.sidebarOpen).toBe(true)
    store.toggleSidebar()
    expect(store.sidebarOpen).toBe(false)
  })

  it('setSidebarWidth persists to localStorage', () => {
    const store = useLayoutStore()
    store.setSidebarWidth(350)
    expect(localStorage.getItem('flowgate.user.guest.layout.sidebarWidth')).toBe('350')
  })

  it('defaults to the approved frame layout: files folded and documents expanded', () => {
    const store = useLayoutStore()
    expect(store.fileExplorerCollapsed).toBe(true)
    expect(store.documentExplorerCollapsed).toBe(false)
  })

  it('toggles explorer frames independently and persists both states', () => {
    const store = useLayoutStore()
    store.toggleFileExplorer()
    expect(store.fileExplorerCollapsed).toBe(false)
    expect(store.documentExplorerCollapsed).toBe(false)

    store.toggleDocumentExplorer()
    expect(store.fileExplorerCollapsed).toBe(false)
    expect(store.documentExplorerCollapsed).toBe(true)
    expect(localStorage.getItem('flowgate.user.guest.layout.fileExplorerCollapsed')).toBe('0')
    expect(localStorage.getItem('flowgate.user.guest.layout.documentExplorerCollapsed')).toBe('1')
  })

  it('restores explorer frame states in a fresh store', () => {
    const store = useLayoutStore()
    store.setFileExplorerCollapsed(false)
    store.setDocumentExplorerCollapsed(true)

    setActivePinia(createPinia())
    const restored = useLayoutStore()
    expect(restored.fileExplorerCollapsed).toBe(false)
    expect(restored.documentExplorerCollapsed).toBe(true)
  })
})

// ─── useTabsStore ──────────────────────────────────────────────────────────────

describe('useTabsStore', () => {
  const makeTab = (id: string) => ({
    id,
    title: `Tab ${id}`,
    path: `/path/${id}.md`,
    type: 'md' as const,
    mdPath: `/path/${id}.md`,
  })

  it('openTab adds a new tab and sets it active', () => {
    const store = useTabsStore()
    store.openTab(makeTab('a'))
    expect(store.tabs).toHaveLength(1)
    expect(store.activeTabId).toBe('a')
  })

  it('openTab with duplicate id just activates existing tab', () => {
    const store = useTabsStore()
    store.openTab(makeTab('a'))
    store.openTab(makeTab('b'))
    store.openTab(makeTab('a'))
    expect(store.tabs).toHaveLength(2)
    expect(store.activeTabId).toBe('a')
  })

  it('activeTab computed returns the active tab object', () => {
    const store = useTabsStore()
    store.openTab(makeTab('x'))
    expect(store.activeTab?.id).toBe('x')
    expect(store.activeTab?.title).toBe('Tab x')
  })

  it('closeTab removes the tab and activates previous', () => {
    const store = useTabsStore()
    store.openTab(makeTab('a'))
    store.openTab(makeTab('b'))
    store.openTab(makeTab('c'))
    store.closeTab('c')
    expect(store.tabs).toHaveLength(2)
    expect(store.activeTabId).toBe('b')
  })

  it('closeTab on only tab sets activeTabId to null', () => {
    const store = useTabsStore()
    store.openTab(makeTab('only'))
    store.closeTab('only')
    expect(store.tabs).toHaveLength(0)
    expect(store.activeTabId).toBeNull()
  })

  it('reorderTabs moves tab from one index to another', () => {
    const store = useTabsStore()
    store.openTab(makeTab('a'))
    store.openTab(makeTab('b'))
    store.openTab(makeTab('c'))
    store.reorderTabs(0, 2) // move 'a' to end
    expect(store.tabs.map((t) => t.id)).toEqual(['b', 'c', 'a'])
  })

  it('closeAll empties tabs and resets activeTabId', () => {
    const store = useTabsStore()
    store.openTab(makeTab('a'))
    store.openTab(makeTab('b'))
    store.closeAll()
    expect(store.tabs).toHaveLength(0)
    expect(store.activeTabId).toBeNull()
  })
})

// ─── useExplorerStore ──────────────────────────────────────────────────────────

describe('useExplorerStore', () => {
  it('invalidateProject removes cached tree', () => {
    const store = useExplorerStore()
    store.fileTreeCache['proj-1'] = [
      { id: 'f1', parent_id: null, type: 'file', name: 'test.md', label: 'Test', path: '/test.md', permissions: ['read'] },
    ]
    store.groupTreeCache['proj-1'] = []
    store.invalidateProject('proj-1')
    expect(store.fileTreeCache['proj-1']).toBeUndefined()
    expect(store.groupTreeCache['proj-1']).toBeUndefined()
  })

  it('selectedFileNodeId is null by default', () => {
    const store = useExplorerStore()
    expect(store.selectedFileNodeId).toBeNull()
  })
})

// ─── useErrorStore ─────────────────────────────────────────────────────────────

describe('useErrorStore', () => {
  it('addError appends to errors list', () => {
    const store = useErrorStore()
    store.addError({ code: 500, message: 'Server error' })
    expect(store.errors).toHaveLength(1)
    expect(store.errors[0].code).toBe(500)
    expect(store.errors[0].message).toBe('Server error')
  })

  it('removeError removes by id', () => {
    const store = useErrorStore()
    store.addError({ code: 403, message: 'Forbidden' })
    const id = store.errors[0].id
    store.removeError(id)
    expect(store.errors).toHaveLength(0)
  })

  it('clearErrors empties the list', () => {
    const store = useErrorStore()
    store.addError({ code: 500, message: 'error1' })
    store.addError({ code: 404, message: 'error2' })
    store.clearErrors()
    expect(store.errors).toHaveLength(0)
  })
})
