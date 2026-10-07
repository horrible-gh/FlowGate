<template>
  <!-- flowgate.v02.0025 — Designer Shell (0003-D §2.1): the Designer route screen.
       DesignerShell > Top + Body, Body > Left + Center + Right, Center > Workspace + Bottom.
       The Left Dock (0023/0024 Explorers) and the Workspace (0026 Designer Center: Flow Tabs +
       read-only Canvas) are real; Top is the final container with a placeholder inside.
       flowgate.v02.0036: the Bottom Dock holds the real Inspector Frame (0035 v7) next to a
       Minimap placeholder. flowgate.v02.0037: the Right Dock is the Tool Panel holding the
       real Palette / Conversation / Git parts, fed the same Project and active Flow as the
       left Dock. -->
  <div class="designer-shell" data-test="designer-shell">
    <DesignerTop />
    <DesignerBody :left-width="leftColumnWidth(layout)" :right-width="rightColumnWidth(layout)">
      <DesignerLeftDock
        :project-id="projectId"
        :active-flow="activeFlowRef"
        :mode="layout.activeLeftMode"
        :collapsed="layout.leftCollapsed"
        @update:mode="layout.activeLeftMode = $event"
        @collapse="layout.leftCollapsed = true"
        @expand="layout.leftCollapsed = false"
        @activate-flow="onActivateFlow"
      />
      <DesignerCenterColumn :center="center" :bottom-height="layout.bottomHeight" />
      <DesignerRightDock
        :project-id="projectId"
        :active-flow="activeFlowRef"
        :tool="layout.activeRightTool"
        :collapsed="layout.rightCollapsed"
        @update:tool="layout.activeRightTool = $event"
      />
    </DesignerBody>
  </div>
</template>

<script setup lang="ts">
// State owned here (0003-D §3.8): the layout state (designerLayout.ts) and the Designer Center
// session (flowgate.v02.0026 — center/designerCenterSession.ts), which this Shell only creates
// and keeps for its own lifetime. Open Flow tabs, the active Flow, Graphs, Selection and
// Viewport all live in that session; the Shell feeds it the current Project and the Project
// Explorer's Flow activation, and hands its active FlowRef to the left Dock and the right Tool
// Panel, so the Project Explorer mark, the File Explorer target, the Conversation / Git scope
// and the active tab are one and the same value.
// Project / Group / Flow / file data stays with the Explorers and features/project; the Shell
// calls no API. It reads the current Project id once and hands the same value to both
// Explorers and the right Tool Panel, so they always look at one Project.
//
// File Explorer intents (select / open / open-diff / context) and Project Explorer plain
// selection are intentionally not handled yet — their targets (Viewer, Modeless Window) do
// not exist (0003-D DEFERRED).
import { computed, reactive, watch } from 'vue'
import { useProjectStore } from '@/features/project'
import { createDesignerCenterSession } from '../center/designerCenterSession'
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

const center = createDesignerCenterSession()

// The Project Explorer's Flow id is the v0.1 group id, which is exactly FlowRef.groupId of
// the File Explorer — no lookup or conversion needed.
const activeFlowRef = center.activeFlowRef

// The session ignores an activation for another Project.
function onActivateFlow(payload: ProjectExplorerFlowEvent) {
  center.openFlow(payload)
}

// A Flow of the previous Project is never carried over: a new Project clears the Center.
watch(projectId, (id) => center.setProject(id), { immediate: true })
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
