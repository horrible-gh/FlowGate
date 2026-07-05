import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export interface Tab {
  id: string
  title: string
  path: string
  type: 'md' | 'text' | 'unsupported' | 'too_large' | 'qtui'
  mdPath?: string | null
  typeCode?: string | null
  modifiedBy?: string | null
  modifiedAt?: string | null
  readonly?: boolean
  projectId?: string | null
}

function getUserId(): string {
  try {
    const token = window.__accessToken__
    if (!token) return 'guest'
    const payload = JSON.parse(atob(token.split('.')[1]))
    return String(payload.sub ?? payload.user_id ?? 'guest')
  } catch {
    return 'guest'
  }
}

function tabsLsKey(): string {
  return `flowgate.user.${getUserId()}.tabs`
}

function loadTabsFromStorage(): { tabs: Tab[]; activeTabId: string | null } {
  try {
    const raw = localStorage.getItem(tabsLsKey())
    if (!raw) return { tabs: [], activeTabId: null }
    const parsed = JSON.parse(raw)
    const loadedTabs: Tab[] = Array.isArray(parsed.tabs) ? parsed.tabs : []
    const loadedActiveId: string | null = parsed.activeTabId ?? null
    return { tabs: loadedTabs, activeTabId: loadedActiveId }
  } catch {
    return { tabs: [], activeTabId: null }
  }
}

function saveTabsToStorage(tabs: Tab[], activeTabId: string | null): void {
  try {
    localStorage.setItem(tabsLsKey(), JSON.stringify({ tabs, activeTabId }))
  } catch {
    /* ignore quota errors */
  }
}

function hasText(value: string | null | undefined): value is string {
  return value != null && value !== ''
}

export function isFileTab(tab: Tab): boolean {
  return tab.projectId != null && !hasText(tab.typeCode)
}

function mergeIncomingTabMetadata(existing: Tab, incoming: Tab): void {
  if (!hasText(existing.typeCode) && hasText(incoming.typeCode)) {
    existing.typeCode = incoming.typeCode
  }
  if (!hasText(existing.path) && hasText(incoming.path)) {
    existing.path = incoming.path
  }
  if (hasText(incoming.mdPath)) {
    existing.mdPath = incoming.mdPath
  }
}

export const useTabsStore = defineStore('tabs', () => {
  const persisted = loadTabsFromStorage()
  const tabs = ref<Tab[]>(persisted.tabs)
  const activeTabId = ref<string | null>(persisted.activeTabId)
  const openTabRequestSeq = ref(0)

  const activeTab = computed<Tab | null>(
    () => tabs.value.find((t) => t.id === activeTabId.value) ?? null,
  )

  function openTab(tab: Tab) {
    openTabRequestSeq.value += 1
    const existing = tabs.value.find((t) => t.id === tab.id)
    if (existing) {
      // File tabs (from FileExplorer) carry projectId but no typeCode.
      // Always refresh type and projectId so stale localStorage data is overwritten.
      const incomingIsFileTab = isFileTab(tab)
      if (incomingIsFileTab) {
        existing.title = tab.title
        existing.path = tab.path
        existing.type = tab.type
        existing.mdPath = tab.mdPath
        existing.projectId = tab.projectId
      } else if (isFileTab(existing)) {
        // Keep file-tab identity; ignore doc-tab metadata from mismatched opens.
      } else {
        if (existing.type === 'unsupported' && tab.type !== 'unsupported') {
          existing.type = tab.type
          if (tab.projectId) existing.projectId = tab.projectId
        }
        mergeIncomingTabMetadata(existing, tab)
      }
      activeTabId.value = tab.id
      return
    }
    tabs.value.push(tab)
    activeTabId.value = tab.id
  }

  function closeTab(id: string) {
    const idx = tabs.value.findIndex((t) => t.id === id)
    if (idx === -1) return
    tabs.value.splice(idx, 1)
    if (activeTabId.value === id) {
      activeTabId.value = tabs.value[Math.max(0, idx - 1)]?.id ?? null
    }
  }

  function setTabTitle(id: string, title: string) {
    const tab = tabs.value.find((t) => t.id === id)
    if (tab) tab.title = title
  }

  function reorderTabs(fromIdx: number, toIdx: number) {
    const [tab] = tabs.value.splice(fromIdx, 1)
    tabs.value.splice(toIdx, 0, tab)
  }

  function closeAll() {
    tabs.value = []
    activeTabId.value = null
  }

  function closeOthers(id: string) {
    const tab = tabs.value.find((t) => t.id === id)
    if (!tab) return
    tabs.value = [tab]
    activeTabId.value = id
  }

  watch([tabs, activeTabId], () => {
    saveTabsToStorage(tabs.value, activeTabId.value)
  }, { deep: true })

  return {
    tabs,
    activeTabId,
    activeTab,
    openTabRequestSeq,
    openTab,
    closeTab,
    setTabTitle,
    reorderTabs,
    closeAll,
    closeOthers,
  }
})
