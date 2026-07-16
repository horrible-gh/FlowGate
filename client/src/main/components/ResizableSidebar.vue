<template>
  <aside
    class="app-sidebar"
    :class="{ open: layoutStore.sidebarOpen }"
  >
    <!-- Top panel: File Explorer -->
    <div
      class="sdb-panel sdb-panel-frame sdb-panel-top"
      :class="{ 'is-collapsed': layoutStore.fileExplorerCollapsed }"
      :style="topPanelStyle"
      data-test="file-explorer-frame"
    >
      <slot name="top" />
    </div>

    <!-- The split ratio only has meaning while both frames are expanded. -->
    <div
      v-if="bothExpanded"
      class="sdb-resizer"
      role="separator"
      aria-orientation="horizontal"
      data-test="explorer-frame-resizer"
      @mousedown="startHResize"
    >
      <div class="sdb-rgrip"></div>
    </div>

    <!-- Bottom panel: Document Explorer -->
    <div
      class="sdb-panel sdb-panel-frame sdb-panel-bottom"
      :class="{ 'is-collapsed': layoutStore.documentExplorerCollapsed }"
      :style="bottomPanelStyle"
      data-test="document-explorer-frame"
    >
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
import { computed } from 'vue'
import { useLayoutStore } from '../stores/layout'

const layoutStore = useLayoutStore()
const bothExpanded = computed(
  () => !layoutStore.fileExplorerCollapsed && !layoutStore.documentExplorerCollapsed,
)

const topPanelStyle = computed(() => {
  if (layoutStore.fileExplorerCollapsed) return { flex: '0 0 35px' }
  if (layoutStore.documentExplorerCollapsed) return { flex: '1 1 auto' }
  return { flex: layoutStore.fileExplorerRatio * 10 }
})

const bottomPanelStyle = computed(() => {
  if (layoutStore.documentExplorerCollapsed) return { flex: '0 0 35px' }
  if (layoutStore.fileExplorerCollapsed) return { flex: '1 1 auto' }
  return { flex: (1 - layoutStore.fileExplorerRatio) * 10 }
})

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
  if (!bothExpanded.value) return
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
