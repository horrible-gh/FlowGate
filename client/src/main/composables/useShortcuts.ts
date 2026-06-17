import { useTabsStore } from '../stores/tabs'

export function createShortcutHandler(
  closeActiveTab: () => void,
  openQuickSearch: () => void,
  openRequirementCreate?: () => void,
) {
  return (e: KeyboardEvent) => {
    if (e.ctrlKey && e.key === 'p') {
      e.preventDefault()
      openQuickSearch()
    }
    if (e.ctrlKey && e.key === 'w') {
      e.preventDefault()
      closeActiveTab()
    }
    if (e.altKey && e.key.toLowerCase() === 'n') {
      e.preventDefault()
      if (e.isComposing) return
      const tag = (document.activeElement as HTMLElement)?.tagName?.toLowerCase()
      const isEditable = (document.activeElement as HTMLElement)?.isContentEditable
      if (tag === 'input' || tag === 'textarea' || isEditable) return
      openRequirementCreate?.()
    }
  }
}

export function useShortcuts(onQuickOpen: () => void, onRequirementCreate?: () => void) {
  const tabsStore = useTabsStore()

  const handler = createShortcutHandler(
    () => {
      if (tabsStore.activeTabId) tabsStore.closeTab(tabsStore.activeTabId)
    },
    onQuickOpen,
    onRequirementCreate,
  )

  function register() {
    window.addEventListener('keydown', handler)
  }

  function unregister() {
    window.removeEventListener('keydown', handler)
  }

  return { handler, register, unregister }
}
