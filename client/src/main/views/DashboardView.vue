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
          :refresh-token="explorerRefreshToken"
          :project-id="currentProjectId"
        />
      </template>
      <template #bottom>
        <GroupExplorer
          :refresh-token="explorerRefreshToken"
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
      :overview-refresh-token="overviewRefreshToken"
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
const explorerRefreshToken = ref(0)
const overviewRefreshToken = ref(0)

function refreshAll() {
  const pid = projectStore.currentProjectId
  if (pid) explorerStore.invalidateProject(pid)
  explorerRefreshToken.value += 1
  // 0454 T0007 — an SSE refresh used to leave the MainPanel overview cards stale (rev1 review
  // finding): explorerRefreshToken only reaches GroupExplorer/FileExplorer, and
  // overviewRefreshToken previously only bumped from the manual refresh button below. Both
  // refresh paths still bump it, for MainPanel's own overviewRefreshToken watcher (fetchQList,
  // AI-run polling) — but as of rev5 the overview cards themselves no longer depend on this
  // token at all: explorerRefreshToken already drives GroupExplorer's reload(), and that reload
  // now carries the overview-summary aggregate with it (explorer.ts fetchGroupTree), so the
  // cards refresh from the SAME tree fetch instead of a second, separately-timed request.
  overviewRefreshToken.value += 1
}

// Manual overview refresh (button in the overview header). Unlike refreshAll(),
// which only invalidates the explorer tree, this also forces an immediate
// dashboard-card refetch — refreshAll() alone would leave the recent-activity
// and workflow cards stale (see NR0003 §3).
function manualRefresh() {
  const pid = projectStore.currentProjectId
  if (!pid) return
  explorerStore.invalidateProject(pid)
  explorerRefreshToken.value += 1
  dashboardStore.invalidate(pid, true)
  overviewRefreshToken.value += 1
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

  // 0454 T0007 rev6 (rev5 review finding 1) — invalidate (which also records this write's order
  // — explorer.ts's groupTreeWriteOrder) BEFORE the reveal fetch below, not after: a force=true
  // fetchGroupTree call only refuses to join a stale in-flight fetch (e.g. GroupExplorer's own
  // reload(), which is also always force=true and may already be running) if it can see that
  // this write already happened — and it can only see that if this line runs first.
  if (pid) explorerStore.invalidateProject(pid)

  if (payload?.docId && pid) {
    try {
      // 0454 T0006 §4.2 — full variant: the just-created requirement has to be findable and
      // openable here whatever the sidebar's hide toggle is set to.
      const nodes = await explorerStore.fetchGroupTree(pid, true, true)
      const node = nodes.find((n) => n.id === payload.docId)

      if (node) {
        // Expand every ancestor group so the document is revealed (0245 R0001: the
        // store owns tree expansion and persists it, so this no longer waits for the
        // remount below to take effect).
        explorerStore.expandGroupAncestors(pid, nodes, node.id)
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

  // explorerRefreshToken still needs its own bump here (not folded into the invalidateProject
  // call above): GroupExplorer's refresh-token watch is what re-fetches AFTER the reveal above
  // has had its chance to run against a cache invalidateProject already cleared.
  if (pid) explorerRefreshToken.value += 1
}

async function handleRelatedDocCreated(payload: { docId: string; openAfter: boolean; projectId: string }) {
  const pid = payload.projectId || projectStore.currentProjectId

  // 0454 T0007 rev6 (rev5 review finding 1) — same reordering as handleRequirementCreated above:
  // invalidate (recording this write's order) before the reveal fetch, not after.
  if (pid) explorerStore.invalidateProject(pid)

  if (payload.docId && pid) {
    try {
      // 0454 T0006 §4.2 — full variant, same reason as the requirement path above: a related
      // document can be created into a group the sidebar is currently hiding.
      const nodes = await explorerStore.fetchGroupTree(pid, true, true)
      const node = nodes.find((n) => n.id === payload.docId)

      if (node) {
        explorerStore.expandGroupAncestors(pid, nodes, node.id)
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

  // See the matching comment in handleRequirementCreated — invalidateProject already ran above.
  if (pid) explorerRefreshToken.value += 1
}

</script>

<style scoped>
</style>
