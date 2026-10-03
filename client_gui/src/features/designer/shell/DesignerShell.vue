<template>
  <!-- flowgate.v02.0025 — Designer Shell (0003-D §2.1): the Designer route screen.
       DesignerShell > Top + Body, Body > Left + Center + Right, Center > Workspace + Bottom.
       Only the Left Dock is real this step; Top / Workspace / Bottom / Right are the final
       containers with a placeholder inside. -->
  <div class="designer-shell" data-test="designer-shell">
    <DesignerTop />
    <DesignerBody :left-width="leftColumnWidth(layout)" :right-width="rightColumnWidth(layout)">
      <DesignerLeftDock
        :project-id="projectId"
        :active-flow="activeFlow"
        :mode="layout.activeLeftMode"
        :collapsed="layout.leftCollapsed"
        @update:mode="layout.activeLeftMode = $event"
        @collapse="layout.leftCollapsed = true"
        @expand="layout.leftCollapsed = false"
        @activate-flow="onActivateFlow"
      />
      <DesignerCenterColumn :active-flow="activeFlow" :bottom-height="layout.bottomHeight" />
      <DesignerRightDock :collapsed="layout.rightCollapsed" />
    </DesignerBody>
  </div>
</template>

<script setup lang="ts">
// State owned here (0003-D §3.8): the layout state (designerLayout.ts) and the one piece of
// Designer context, the active Flow. Project / Group / Flow / file data stays with the
// Explorers and features/project; the Shell calls no API. It reads the current Project id
// once and hands the same value to both Explorers, so they always look at one Project.
//
// File Explorer intents (select / open / open-diff / context) and Project Explorer plain
// selection are intentionally not handled yet — their targets (Viewer, Modeless Window,
// Flow Tabs) do not exist (0003-D DEFERRED).
import { computed, reactive, ref, watch } from 'vue'
import { useProjectStore, type FlowRef } from '@/features/project'
import type { ProjectExplorerFlowEvent } from '../projectExplorer/projectExplorerTree'
import DesignerBody from './DesignerBody.vue'
import DesignerCenterColumn from './DesignerCenterColumn.vue'
import DesignerLeftDock from './DesignerLeftDock.vue'
import DesignerRightDock from './DesignerRightDock.vue'
import DesignerTop from './DesignerTop.vue'
import { createDesignerLayout, leftColumnWidth, rightColumnWidth } from './designerLayout'

const projectStore = useProjectStore()

const layout = reactive(createDesignerLayout())

const projectId = computed<string | null>(() => projectStore.currentProjectId)

// The Project Explorer's Flow id is the v0.1 group id, which is exactly FlowRef.groupId of
// the File Explorer — no lookup or conversion needed.
const activeFlow = ref<FlowRef | null>(null)

function onActivateFlow(payload: ProjectExplorerFlowEvent) {
  if (payload.projectId !== projectId.value) return
  activeFlow.value = { groupId: payload.flowId, name: payload.flowName }
}

// A Flow of the previous Project is never carried over.
watch(projectId, () => {
  activeFlow.value = null
})
</script>

<style scoped>
/* Fills main.gui-route-content, which is the whole viewport on the Designer route (App.vue
   renders no Topbar / Sidebar there), and never scrolls itself; each area scrolls inside. */
.designer-shell {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  color: var(--fg2-text);
  font-family: var(--fg2-font);
}

/* ≤1120px the App Shell scrolls as a page and gives no height; keep the Body usable. The
   final responsive policy is deferred. */
@media (max-width: 1120px) {
  .designer-shell {
    min-height: 100vh;
  }
}
</style>
