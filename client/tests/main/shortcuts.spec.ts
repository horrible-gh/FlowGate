import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createShortcutHandler } from '@main/composables/useShortcuts'
import { useTabsStore } from '@main/stores/tabs'

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('createShortcutHandler', () => {
  it('Ctrl+P calls openQuickSearch and prevents default', () => {
    const openQuickSearch = vi.fn()
    const closeActiveTab = vi.fn()
    const handler = createShortcutHandler(closeActiveTab, openQuickSearch)

    const event = new KeyboardEvent('keydown', { key: 'p', ctrlKey: true, cancelable: true })
    const preventSpy = vi.spyOn(event, 'preventDefault')

    handler(event)

    expect(openQuickSearch).toHaveBeenCalledTimes(1)
    expect(closeActiveTab).not.toHaveBeenCalled()
    expect(preventSpy).toHaveBeenCalled()
  })

  it('Ctrl+W calls closeActiveTab and prevents default', () => {
    const openQuickSearch = vi.fn()
    const closeActiveTab = vi.fn()
    const handler = createShortcutHandler(closeActiveTab, openQuickSearch)

    const event = new KeyboardEvent('keydown', { key: 'w', ctrlKey: true, cancelable: true })
    const preventSpy = vi.spyOn(event, 'preventDefault')

    handler(event)

    expect(closeActiveTab).toHaveBeenCalledTimes(1)
    expect(openQuickSearch).not.toHaveBeenCalled()
    expect(preventSpy).toHaveBeenCalled()
  })

  it('unrelated key does nothing', () => {
    const openQuickSearch = vi.fn()
    const closeActiveTab = vi.fn()
    const handler = createShortcutHandler(closeActiveTab, openQuickSearch)

    handler(new KeyboardEvent('keydown', { key: 'a', ctrlKey: false }))

    expect(openQuickSearch).not.toHaveBeenCalled()
    expect(closeActiveTab).not.toHaveBeenCalled()
  })

  it('Alt+N calls openRequirementCreate and prevents default', () => {
    const openQuickSearch = vi.fn()
    const closeActiveTab = vi.fn()
    const openRequirementCreate = vi.fn()
    const handler = createShortcutHandler(closeActiveTab, openQuickSearch, openRequirementCreate)

    const event = new KeyboardEvent('keydown', { key: 'n', altKey: true, cancelable: true })
    const preventSpy = vi.spyOn(event, 'preventDefault')

    handler(event)

    expect(openRequirementCreate).toHaveBeenCalledTimes(1)
    expect(openQuickSearch).not.toHaveBeenCalled()
    expect(closeActiveTab).not.toHaveBeenCalled()
    expect(preventSpy).toHaveBeenCalled()
  })

  it('Alt+N prevents default but does NOT call openRequirementCreate when focus is on input', () => {
    const openQuickSearch = vi.fn()
    const closeActiveTab = vi.fn()
    const openRequirementCreate = vi.fn()
    const handler = createShortcutHandler(closeActiveTab, openQuickSearch, openRequirementCreate)

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()

    const event = new KeyboardEvent('keydown', { key: 'n', altKey: true, cancelable: true })
    const preventSpy = vi.spyOn(event, 'preventDefault')

    handler(event)

    expect(preventSpy).toHaveBeenCalled()          // browser new-window must be blocked
    expect(openRequirementCreate).not.toHaveBeenCalled() // modal must NOT open
    document.body.removeChild(input)
  })

  it('Ctrl+P with active tab does not close tab', () => {
    const store = useTabsStore()
    store.openTab({ id: 'tab1', title: 'Tab1', path: '/file.md', type: 'md' })

    const openQuickSearch = vi.fn()
    const closeActiveTab = vi.fn()
    const handler = createShortcutHandler(closeActiveTab, openQuickSearch)

    handler(new KeyboardEvent('keydown', { key: 'p', ctrlKey: true, cancelable: true }))

    expect(openQuickSearch).toHaveBeenCalledTimes(1)
    expect(store.tabs).toHaveLength(1) // tab still open
  })
})
