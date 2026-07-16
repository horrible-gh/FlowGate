import { defineStore } from 'pinia'
import { ref } from 'vue'

function getUserIdFromToken(): string {
  try {
    const token = window.__accessToken__
    if (!token) return 'guest'
    const payload = JSON.parse(atob(token.split('.')[1]))
    return String(payload.sub ?? payload.user_id ?? 'guest')
  } catch {
    return 'guest'
  }
}

function makeKey(suffix: string): string {
  return `flowgate.user.${getUserIdFromToken()}.layout.${suffix}`
}

function readBoolean(suffix: string, fallback: boolean): boolean {
  const stored = localStorage.getItem(makeKey(suffix))
  if (stored === null) return fallback
  return stored === '1' || stored === 'true'
}

export const useLayoutStore = defineStore('layout', () => {
  const sidebarWidth = ref(
    parseInt(localStorage.getItem(makeKey('sidebarWidth')) || '') || 280,
  )
  const fileExplorerRatio = ref(
    parseFloat(localStorage.getItem(makeKey('fileExplorerRatio')) || '') || 0.5,
  )
  // Frame accordions are independent. Defaults mirror the approved prototype:
  // files folded, documents expanded. Keeping them in this user-scoped store also
  // survives project/SSE-driven explorer remounts.
  const fileExplorerCollapsed = ref(readBoolean('fileExplorerCollapsed', true))
  const documentExplorerCollapsed = ref(readBoolean('documentExplorerCollapsed', false))
  const sidebarOpen = ref(false)

  function setSidebarWidth(w: number) {
    sidebarWidth.value = Math.max(180, Math.min(480, w))
    localStorage.setItem(makeKey('sidebarWidth'), String(sidebarWidth.value))
  }

  function setFileExplorerRatio(r: number) {
    fileExplorerRatio.value = Math.max(0.15, Math.min(0.85, r))
    localStorage.setItem(makeKey('fileExplorerRatio'), String(fileExplorerRatio.value))
  }

  function setFileExplorerCollapsed(collapsed: boolean) {
    fileExplorerCollapsed.value = collapsed
    localStorage.setItem(makeKey('fileExplorerCollapsed'), collapsed ? '1' : '0')
  }

  function setDocumentExplorerCollapsed(collapsed: boolean) {
    documentExplorerCollapsed.value = collapsed
    localStorage.setItem(makeKey('documentExplorerCollapsed'), collapsed ? '1' : '0')
  }

  function toggleFileExplorer() {
    setFileExplorerCollapsed(!fileExplorerCollapsed.value)
  }

  function toggleDocumentExplorer() {
    setDocumentExplorerCollapsed(!documentExplorerCollapsed.value)
  }

  function toggleSidebar() {
    sidebarOpen.value = !sidebarOpen.value
  }

  return {
    sidebarWidth,
    fileExplorerRatio,
    fileExplorerCollapsed,
    documentExplorerCollapsed,
    sidebarOpen,
    setSidebarWidth,
    setFileExplorerRatio,
    setFileExplorerCollapsed,
    setDocumentExplorerCollapsed,
    toggleFileExplorer,
    toggleDocumentExplorer,
    toggleSidebar,
  }
})
