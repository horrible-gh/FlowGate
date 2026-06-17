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

export const useLayoutStore = defineStore('layout', () => {
  const sidebarWidth = ref(
    parseInt(localStorage.getItem(makeKey('sidebarWidth')) || '') || 280,
  )
  const fileExplorerRatio = ref(
    parseFloat(localStorage.getItem(makeKey('fileExplorerRatio')) || '') || 0.5,
  )
  const sidebarOpen = ref(false)

  function setSidebarWidth(w: number) {
    sidebarWidth.value = Math.max(180, Math.min(480, w))
    localStorage.setItem(makeKey('sidebarWidth'), String(sidebarWidth.value))
  }

  function setFileExplorerRatio(r: number) {
    fileExplorerRatio.value = Math.max(0.15, Math.min(0.85, r))
    localStorage.setItem(makeKey('fileExplorerRatio'), String(fileExplorerRatio.value))
  }

  function toggleSidebar() {
    sidebarOpen.value = !sidebarOpen.value
  }

  return { sidebarWidth, fileExplorerRatio, sidebarOpen, setSidebarWidth, setFileExplorerRatio, toggleSidebar }
})
