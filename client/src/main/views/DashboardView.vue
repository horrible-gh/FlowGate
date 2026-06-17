<template>
  <div class="app-shell" :style="shellStyle">
    <AppHeader
      :sidebar-open="layoutStore.sidebarOpen"
      show-sidebar-toggle
      @toggle-sidebar="layoutStore.toggleSidebar"
    />
    <ResizableSidebar>
      <template #top>
        <FileExplorer
          :key="`files-${currentProjectId ?? 'none'}-${explorerRefreshKey}`"
          :project-id="currentProjectId"
        />
      </template>
      <template #bottom>
        <GroupExplorer
          :key="`groups-${currentProjectId ?? 'none'}-${explorerRefreshKey}`"
          :project-id="currentProjectId"
          @create-requirement="openRequirementModal"
        />
      </template>
    </ResizableSidebar>
    <button
      v-if="layoutStore.sidebarOpen"
      class="sidebar-backdrop"
      type="button"
      :aria-label="t('main.nav.close_explorer')"
      @click="layoutStore.toggleSidebar"
    />
    <MainPanel
      @create-requirement="openRequirementModal"
      @related-doc-created="handleRelatedDocCreated"
      @refresh-overview="manualRefresh"
    />
    <NewRequirementModal
      v-if="showRequirementModal"
      :initial-group-id="initialRequirementGroupId"
      @close="showRequirementModal = false"
      @created="handleRequirementCreated"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProjectStore } from '../stores/project'
import { useLayoutStore } from '../stores/layout'
import { useExplorerStore } from '../stores/explorer'
import { useDashboardStore } from '../stores/dashboard'
import { useTabsStore } from '../stores/tabs'
import { useFlowGateSse } from '../composables/useFlowGateSse'
import AppHeader from '../components/AppHeader.vue'
import ResizableSidebar from '../components/ResizableSidebar.vue'
import FileExplorer from '../components/FileExplorer.vue'
import GroupExplorer from '../components/GroupExplorer.vue'
import MainPanel from '../components/MainPanel.vue'
import NewRequirementModal from '../components/NewRequirementModal.vue'

const projectStore = useProjectStore()
const layoutStore = useLayoutStore()
const explorerStore = useExplorerStore()
const dashboardStore = useDashboardStore()
const tabsStore = useTabsStore()
const { t } = useI18n()
const currentProjectId = computed(() => projectStore.currentProjectId)
const shellStyle = computed(() => ({
  '--sdb-w': `${layoutStore.sidebarWidth}px`,
}))
const showRequirementModal = ref(false)
const initialRequirementGroupId = ref<string | null>(null)
const explorerRefreshKey = ref(0)

function refreshAll() {
  const pid = projectStore.currentProjectId
  if (pid) explorerStore.invalidateProject(pid)
  explorerRefreshKey.value += 1
}

// Manual overview refresh (button in the overview header). Unlike refreshAll(),
// which only invalidates the explorer tree, this also forces an immediate
// dashboard-card refetch — refreshAll() alone would leave the recent-activity
// and workflow cards stale (see NR0003 §3).
function manualRefresh() {
  const pid = projectStore.currentProjectId
  if (!pid) return
  explorerStore.invalidateProject(pid)
  explorerRefreshKey.value += 1
  dashboardStore.invalidate(pid, true)
}

useFlowGateSse(refreshAll)

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape' && layoutStore.sidebarOpen) {
    layoutStore.toggleSidebar()
    return
  }
  if (!e.altKey || e.key.toLowerCase() !== 'n') return
  // Always prevent the browser's Ctrl+N (new window) — even when focus is on
  // an input/textarea, we must call preventDefault() here so Chrome does not
  // open a new window. The guard below only controls whether the modal opens.
  e.preventDefault()
  if (e.isComposing) return
  const tag = (document.activeElement as HTMLElement)?.tagName?.toLowerCase()
  const isEditable = (document.activeElement as HTMLElement)?.isContentEditable
  if (tag === 'input' || tag === 'textarea' || isEditable) return
  if (showRequirementModal.value) return
  openRequirementModal()
}

onMounted(() => window.addEventListener('keydown', onKeyDown, true))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown, true))

function openRequirementModal(payload?: { groupId?: string }) {
  initialRequirementGroupId.value = payload?.groupId ?? null
  showRequirementModal.value = true
}

async function handleRequirementCreated(payload?: { docId?: string; openAfter?: boolean }) {
  showRequirementModal.value = false
  const pid = projectStore.currentProjectId

  if (payload?.docId && pid) {
    try {
      const nodes = await explorerStore.fetchGroupTree(pid, true)
      const node = nodes.find((n) => n.id === payload.docId)

      if (node) {
        // Expand all ancestor groups in localStorage so they're open after remount
        let parentId = node.parent_id
        while (parentId) {
          try {
            localStorage.setItem(`flowgate:grp-exp:${pid}:${parentId}`, '1')
          } catch { /* ignore */ }
          const parent = nodes.find((n) => n.id === parentId)
          parentId = parent?.parent_id ?? null
        }
        explorerStore.selectedGroupNodeId = payload.docId

        if (payload.openAfter && node.node_type === 'document') {
          tabsStore.openTab({
            id: node.id,
            title: node.label,
            path: node.md_path ?? '',
            type: node.type_code === 'Q' ? 'qtui' : node.has_md ? 'md' : 'unsupported',
            mdPath: node.md_path,
            typeCode: node.type_code,
          })
        }
      }
    } catch {
      /* If opening the document fails, the explorer is already refreshed — ignore the error */
    }
  }

  if (pid) {
    explorerStore.invalidateProject(pid)
    explorerRefreshKey.value += 1
  }
}

async function handleRelatedDocCreated(payload: { docId: string; openAfter: boolean; projectId: string }) {
  const pid = payload.projectId || projectStore.currentProjectId

  if (payload.docId && pid) {
    try {
      const nodes = await explorerStore.fetchGroupTree(pid, true)
      const node = nodes.find((n) => n.id === payload.docId)

      if (node) {
        let parentId = node.parent_id
        while (parentId) {
          try {
            localStorage.setItem(`flowgate:grp-exp:${pid}:${parentId}`, '1')
          } catch { /* ignore */ }
          const parent = nodes.find((n) => n.id === parentId)
          parentId = parent?.parent_id ?? null
        }
        explorerStore.selectedGroupNodeId = payload.docId

        if (payload.openAfter && node.node_type === 'document') {
          tabsStore.openTab({
            id: node.id,
            title: node.label,
            path: node.md_path ?? '',
            type: node.type_code === 'Q' ? 'qtui' : node.has_md ? 'md' : 'unsupported',
            mdPath: node.md_path,
            typeCode: node.type_code,
          })
        }
      }
    } catch {
      /* If opening the document fails, the explorer is already refreshed — ignore the error */
    }
  }

  if (pid) {
    explorerStore.invalidateProject(pid)
    explorerRefreshKey.value += 1
  }
}

</script>

<style scoped>
</style>
