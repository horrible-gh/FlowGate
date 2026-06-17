<template>
  <aside
    class="app-sidebar"
    :class="{ open: layoutStore.sidebarOpen }"
  >
    <!-- Top panel: File Explorer -->
    <div class="sdb-panel sdb-panel-top" :style="{ flex: layoutStore.fileExplorerRatio * 10 }">
      <slot name="top" />
    </div>

    <!-- Horizontal resizer -->
    <div
      class="sdb-resizer"
      role="separator"
      aria-orientation="horizontal"
      @mousedown="startHResize"
    >
      <div class="sdb-rgrip"></div>
    </div>

    <!-- Bottom panel: Group Explorer -->
    <div class="sdb-panel sdb-panel-bottom" :style="{ flex: (1 - layoutStore.fileExplorerRatio) * 10 }">
      <slot name="bottom" />
    </div>

    <!-- Vertical resize handle (right edge) -->
    <div
      class="sdb-v-handle"
      role="separator"
      aria-orientation="vertical"
      @mousedown="startVResize"
    />
  </aside>
</template>

<script setup lang="ts">
import { useLayoutStore } from '../stores/layout'

const layoutStore = useLayoutStore()

function startVResize(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const startW = layoutStore.sidebarWidth

  function onMove(ev: MouseEvent) {
    layoutStore.setSidebarWidth(startW + ev.clientX - startX)
  }

  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }

  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

function startHResize(e: MouseEvent) {
  e.preventDefault()
  const sidebar = (e.target as HTMLElement).closest('.app-sidebar') as HTMLElement | null
  if (!sidebar) return
  const totalH = sidebar.clientHeight
  const startY = e.clientY
  const startRatio = layoutStore.fileExplorerRatio

  function onMove(ev: MouseEvent) {
    const delta = ev.clientY - startY
    layoutStore.setFileExplorerRatio(startRatio + delta / totalH)
  }

  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }

  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
</script>
