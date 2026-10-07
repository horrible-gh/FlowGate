<template>
  <!-- flowgate.v02.0025 — Designer Shell (0003-D §2.1): the Designer route screen.
       DesignerShell > Top + Body, Body > Left + Center + Right, Center > Workspace + Bottom.
       The Left Dock (0023/0024 Explorers) and the Workspace (0026 Designer Center: Flow Tabs +
       read-only Canvas) are real; Top was the final container with a placeholder inside.
       flowgate.v02.0036: the Bottom Dock holds the real Inspector Frame (0035 v7) next to a
       Minimap placeholder. flowgate.v02.0037: the Right Dock is the Tool Panel holding the
       real Palette / Conversation / Git parts, fed the same Project and active Flow as the
       left Dock. flowgate.v02.0038: the Top holds the real Header / Menu Bar / Toolbar.
       flowgate.v02.0039 T#3: until the user chooses something in the Project Explorer (or a
       Flow is open) the Designer shows its initial unselected state — every area keeps its
       frame, the work controls are unavailable and nothing is unfolded. -->
  <div class="designer-shell" data-test="designer-shell">
    <DesignerTop
      :run-mode="top.runMode"
      :density="top.density"
      :zoom="zoom"
      :window-state="windowState"
      :has-work-target="hasWorkTarget"
      @command="onTopCommand"
      @toggle-panel="onTogglePanel"
      @show-tab="onShowTab"
      @update:density="top.density = $event"
    />
    <DesignerBody :left-width="leftColumnWidth(layout)" :right-width="rightColumnWidth(layout)">
      <DesignerLeftDock
        :project-id="projectId"
        :active-flow="activeFlowRef"
        :mode="layout.activeLeftMode"
        :collapsed="layout.leftCollapsed"
        :unselected="!hasWorkTarget"
        @update:mode="layout.activeLeftMode = $event"
        @collapse="layout.leftCollapsed = true"
        @expand="layout.leftCollapsed = false"
        @activate-flow="onActivateFlow"
        @update:selection="explorerSelection = $event"
      />
      <DesignerCenterColumn :center="center" :bottom-height="layout.bottomHeight" :unselected="!hasWorkTarget" />
      <DesignerRightDock
        :project-id="projectId"
        :active-flow="activeFlowRef"
        :tool="layout.activeRightTool"
        :collapsed="layout.rightCollapsed"
        :unselected="!hasWorkTarget"
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
//
// flowgate.v02.0038 — the top band's state (top/designerTop.ts: run button state, layout
// density) is owned here next to the layout. Of the top commands the Shell handles those whose
// target already exists: the Window menu shows / hides the left and right Docks and moves to
// their tabs through the same layout fields the Docks use, and the run buttons switch to
// ■ 중지 and back. Everything else — Project / Group / Flow commands, edit, run execution,
// Canvas zoom, search, Inspector / Minimap display, help, notifications, AI, settings, user —
// is left to later Feature groups (0001-R §8).
//
// flowgate.v02.0039 T#3 — work target. The Project context (the current Project id) is not a
// selection (0039 0001-R §4), so entering the Designer leaves it without a work target: the
// Project Explorer shows the tree with nothing chosen, and every other area shows the confirmed
// initial-state design (15-designer-shell-initial-state-v7) — Toolbar and target-bound menu
// items unavailable, an empty Center frame, the Inspector frame only, the Minimap, the right
// Tool Panel and the left navigation chrome dimmed. The first explicit choice in the Explorer
// (the Explorer reports its selection through `update:selection`) or an open Flow is the work
// target, and the Designer continues exactly as before. A Project change clears both, so the
// new Project starts unselected again.
import { computed, reactive, ref, watch } from 'vue'
import { useProjectStore } from '@/features/project'
import { createDesignerCenterSession } from '../center/designerCenterSession'
import type { ExplorerSelection, ProjectExplorerFlowEvent } from '../projectExplorer/projectExplorerTree'
import {
  createDesignerTopState,
  nextRunMode,
  zoomPercent,
  type DesignerTopCommand,
  type DesignerWindowPanel,
  type DesignerWindowState,
  type DesignerWindowTab,
} from '../top/designerTop'
import DesignerBody from './DesignerBody.vue'
import DesignerCenterColumn from './DesignerCenterColumn.vue'
import DesignerLeftDock from './DesignerLeftDock.vue'
import DesignerRightDock from './DesignerRightDock.vue'
import DesignerTop from './DesignerTop.vue'
import { createDesignerLayout, leftColumnWidth, rightColumnWidth } from './designerLayout'

const projectStore = useProjectStore()

const layout = reactive(createDesignerLayout())

const top = reactive(createDesignerTopState())

const projectId = computed<string | null>(() => projectStore.currentProjectId)

const center = createDesignerCenterSession()

// The Project Explorer's Flow id is the v0.1 group id, which is exactly FlowRef.groupId of
// the File Explorer — no lookup or conversion needed.
const activeFlowRef = center.activeFlowRef

// The user's own choice in the Project Explorer, as that part reports it (never the context).
const explorerSelection = ref<ExplorerSelection>(null)

const hasWorkTarget = computed(() => explorerSelection.value !== null || activeFlowRef.value !== null)

// The run buttons' UI state belongs to a target; it does not outlive it.
watch(hasWorkTarget, (has) => {
  if (!has) top.runMode = null
})

// The session ignores an activation for another Project.
function onActivateFlow(payload: ProjectExplorerFlowEvent) {
  center.openFlow(payload)
}

// A Flow of the previous Project is never carried over: a new Project clears the Center.
// The previous Project's choice does not carry over either (0039 T#3). It is dropped here, before
// this render, rather than only when the Explorer reports its own reset a tick later. Otherwise
// the right parts would briefly get the new Project as a work target and start reading its data.
watch(
  projectId,
  (id) => {
    center.setProject(id)
    explorerSelection.value = null
  },
  { immediate: true },
)

// The Toolbar shows the zoom the active Canvas last reported (100% before it reports one).
const zoom = computed(() => zoomPercent(center.activeView.value?.viewport?.zoom))

// Inspector and Minimap have no hide state yet, so the Window menu shows them as displayed.
const windowState = computed<DesignerWindowState>(() => ({
  panels: { left: !layout.leftCollapsed, right: !layout.rightCollapsed, inspector: true, minimap: true },
  activeTabs: [layout.activeLeftMode, layout.activeRightTool],
}))

function onTopCommand(command: DesignerTopCommand) {
  if (!hasWorkTarget.value) return
  top.runMode = nextRunMode(top.runMode, command)
}

function onTogglePanel(panel: DesignerWindowPanel) {
  if (panel === 'left') layout.leftCollapsed = !layout.leftCollapsed
  else if (panel === 'right') layout.rightCollapsed = !layout.rightCollapsed
}

// A tab move opens its panel when hidden, then switches to that tab (0001-R §3.5).
function onShowTab(tab: DesignerWindowTab) {
  if (tab === 'project' || tab === 'file') {
    layout.leftCollapsed = false
    layout.activeLeftMode = tab
  } else {
    layout.rightCollapsed = false
    layout.activeRightTool = tab
  }
}
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
