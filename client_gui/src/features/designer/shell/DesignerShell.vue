<template>
  <!-- flowgate.v02.0025 — Designer Shell (0003-D §2.1): the Designer route screen.
       DesignerShell > Top + Body, Body > Left + Center + Right, Center > Workspace + Bottom.
       The Left Dock (0023/0024 Explorers) and the Workspace (0026 Designer Center: Flow Tabs +
       read-only Canvas) are real; Top was the final container with a placeholder inside.
       flowgate.v02.0036: the Bottom Dock holds the real Inspector Frame (0035 v7) next to a
       Minimap placeholder. flowgate.v02.0037: the Right Dock is the Tool Panel holding the
       real Palette / Conversation / Git parts, fed the same Project and active Flow as the
       left Dock. flowgate.v02.0038: the Top holds the real Header / Menu Bar / Toolbar.
       flowgate.v02.0039 T#3 / v02.0042: until a Flow is open the Designer shows its initial
       unselected state — every area keeps its frame, the work controls are unavailable and
       nothing is unfolded. A plain selection in the Project Explorer does not change that.
       flowgate.v02.0043: the File menu is connected — its dialogs (Group / Flow 열기, 새 Group /
       Flow) are mounted here and act on the same Project context, Explorer and Center. -->
  <div class="designer-shell" data-test="designer-shell">
    <DesignerTop
      :run-mode="top.runMode"
      :density="top.density"
      :zoom="zoom"
      :window-state="windowState"
      :targets="menuTargets"
      :recent="recent"
      @command="onTopCommand"
      @toggle-panel="onTogglePanel"
      @show-tab="onShowTab"
      @update:density="top.density = $event"
      @open-recent="onOpenRecent"
    />
    <DesignerBody :left-width="leftColumnWidth(layout)" :right-width="rightColumnWidth(layout)">
      <DesignerLeftDock
        :project-id="projectId"
        :active-flow="activeFlowRef"
        :mode="layout.activeLeftMode"
        :collapsed="layout.leftCollapsed"
        :unselected="!hasWorkTarget"
        :explorer-request="explorerRequest"
        :refresh-token="explorerRefreshToken"
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

    <DesignerOpenDialog
      :open="dialog === 'group-open'"
      kind="group"
      :project-id="projectId"
      @close="dialog = null"
      @open-group="onGroupChosen"
    />
    <DesignerOpenDialog
      :open="dialog === 'flow-open'"
      kind="flow"
      :project-id="projectId"
      @close="dialog = null"
      @open-flow="onFlowChosen"
    />
    <DesignerGroupCreateDialog :open="dialog === 'group-new'" :project-id="projectId" @close="dialog = null" @created="onGroupCreated" />
    <DesignerFlowCreateDialog
      :open="dialog === 'flow-new'"
      :project-id="projectId"
      :group-id="flowCreateGroupId"
      @close="dialog = null"
      @created="onFlowCreated"
    />
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
// File Explorer intents (select / open / open-diff / context) are intentionally not handled
// yet — their targets (Viewer, Modeless Window) do not exist (0003-D DEFERRED).
//
// flowgate.v02.0038 — the top band's state (top/designerTop.ts: run button state, layout
// density) is owned here next to the layout. Of the top commands the Shell handles those whose
// target already exists: the Window menu shows / hides the left and right Docks and moves to
// their tabs through the same layout fields the Docks use, and the run buttons switch to
// ■ 중지 and back. Edit, run execution, Canvas zoom, search, Inspector / Minimap display,
// help, notifications, AI, settings, user are left to later Feature groups (0001-R §8).
//
// flowgate.v02.0039 T#3 — work target. The Project context (the current Project id) is not a
// selection (0039 0001-R §4), so entering the Designer leaves it without a work target: the
// Project Explorer shows the tree with nothing chosen, and every other area shows the confirmed
// initial-state design (15-designer-shell-initial-state-v7) — Toolbar and target-bound menu
// items unavailable, an empty Center frame, the Inspector frame only, the Minimap, the right
// Tool Panel and the left navigation chrome dimmed.
// flowgate.v02.0042 — only an open Flow is the work target. A plain click in the Explorer is a
// selection, not an opening (click = select, Flow double-click / Enter = select + open), so it
// leaves the Designer unselected. Closing the last tab or changing the Project (which clears the
// Center) returns the Designer to the unselected state.
//
// flowgate.v02.0040 — the Project Dialog's "열기" raises a one-shot explicit-open signal in the
// store. The Project Explorer takes it on entry; whatever it did not take is dropped once this
// Shell is mounted, so the signal never outlives the entry it was raised for.
//
// flowgate.v02.0043 — the File menu (0043 0001-R). Every item reaches the product function that
// already exists, nothing is a separate behaviour of the menu:
//   · 새 Project / Project 열기… / 최근 Project — the Project Dialog and its "열기" (0040): the store
//     opens the Dialog (with the create child for 새 Project) or opens the recent Project the way
//     "열기" does; the Explorer selects the opened Project through the same explicit-open signal.
//   · Project 닫기 — the store clears the current Project; Explorers and Center follow (0042).
//   · 새 Group / Group 열기… / 최근 Group — the Group is selected and opened (its Flow list unfolded)
//     in the Project Explorer; a new Group is created through the store and the tree re-read.
//   · Group 닫기 — the Group chosen in the Explorer is folded and the selection cleared. The
//     Explorer selection is listened to for this alone; it still does not activate anything.
//   · 새 Flow / Flow 열기… / 최근 Flow — the same opening as the Explorer's double click (select +
//     open in the Center); a new Flow is created through the store and the tree re-read.
//   · Flow 닫기 — closes the active Flow tab, as the tab's own close does.
//   · Designer 닫기 — this Shell is router-free; it emits `close-designer` and the App Shell
//     moves to the Dashboard.
// Availability follows each command's precondition (designerTop.ts DesignerMenuTargets). Recent
// Project / Group / Flow entries are recorded in the store when a Project is opened and when
// this Shell opens a Group or Flow; the Group / Flow lists show the current Project's entries.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useProjectStore, type CreatedGroup } from '@/features/project'
import { createDesignerCenterSession } from '../center/designerCenterSession'
import DesignerFlowCreateDialog, { type DesignerCreatedFlow } from '../fileMenu/DesignerFlowCreateDialog.vue'
import DesignerGroupCreateDialog from '../fileMenu/DesignerGroupCreateDialog.vue'
import DesignerOpenDialog from '../fileMenu/DesignerOpenDialog.vue'
import {
  buildExplorerProject,
  projectNodeId,
  type ExplorerFlow,
  type ExplorerGroup,
  type ExplorerSelection,
  type ExplorerStateRequest,
  type ProjectExplorerFlowEvent,
} from '../projectExplorer/projectExplorerTree'
import {
  createDesignerTopState,
  isMenuCommandEnabled,
  nextRunMode,
  zoomPercent,
  type DesignerMenuTargets,
  type DesignerRecentLists,
  type DesignerRecentRequest,
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

const emit = defineEmits<{
  /** File › Designer 닫기: the App Shell leaves the Designer (this Shell owns no router). */
  'close-designer': []
}>()

const projectStore = useProjectStore()

const layout = reactive(createDesignerLayout())

const top = reactive(createDesignerTopState())

const projectId = computed<string | null>(() => projectStore.currentProjectId)

const center = createDesignerCenterSession()

// The Project Explorer's Flow id is the v0.1 group id, which is exactly FlowRef.groupId of
// the File Explorer — no lookup or conversion needed.
const activeFlowRef = center.activeFlowRef

const hasWorkTarget = computed(() => activeFlowRef.value !== null)

// The run buttons' UI state belongs to a target; it does not outlive it.
watch(hasWorkTarget, (has) => {
  if (!has) top.runMode = null
})

// A Flow of the previous Project is never carried over: a new Project clears the Center.
watch(
  projectId,
  (id) => {
    center.setProject(id)
  },
  { immediate: true },
)

onMounted(() => {
  projectStore.takeExplicitOpen()
})

// The Toolbar shows the zoom the active Canvas last reported (100% before it reports one).
const zoom = computed(() => zoomPercent(center.activeView.value?.viewport?.zoom))

// Inspector and Minimap have no hide state yet, so the Window menu shows them as displayed.
const windowState = computed<DesignerWindowState>(() => ({
  panels: { left: !layout.leftCollapsed, right: !layout.rightCollapsed, inspector: true, minimap: true },
  activeTabs: [layout.activeLeftMode, layout.activeRightTool],
}))

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

// --- File menu (flowgate.v02.0043) ---------------------------------------------------------------

type FileMenuDialog = 'group-open' | 'flow-open' | 'group-new' | 'flow-new' | null

const dialog = ref<FileMenuDialog>(null)

// The Project Explorer's current selection, kept for Group 닫기 (which Group to fold) and for the
// default Group of 새 Flow. It is not a work target (0042): nothing here activates on it.
const explorerSelection = ref<ExplorerSelection>(null)

const menuTargets = computed<DesignerMenuTargets>(() => ({
  project: projectId.value !== null,
  group: explorerSelection.value?.kind === 'group',
  flow: hasWorkTarget.value,
}))

// The recent lists the Menu Bar shows: every opened Project; the Groups / Flows of the current
// Project only (a Flow of another Project could not be opened in this Center anyway).
const recent = computed<DesignerRecentLists>(() => {
  const pid = projectId.value
  return {
    project: projectStore.recentProjects.map(({ id, name }) => ({ id, name })),
    group: pid ? projectStore.recentGroups.filter((g) => g.projectId === pid).map(({ id, name }) => ({ id, name })) : [],
    flow: pid ? projectStore.recentFlows.filter((f) => f.projectId === pid).map(({ id, name }) => ({ id, name })) : [],
  }
})

// What the Shell asks the Project Explorer to show; each request is applied once (seq).
const explorerRequest = ref<ExplorerStateRequest | null>(null)
let explorerRequestSeq = 0

function requestExplorer(request: Omit<ExplorerStateRequest, 'seq'>) {
  explorerRequest.value = { ...request, seq: ++explorerRequestSeq }
  // The result is in the Project Explorer: bring that Explorer to the front of the left Dock.
  layout.activeLeftMode = 'project'
}

// Bumped after a Group / Flow was created so the Explorer re-reads the tree it shows.
const explorerRefreshToken = ref(0)

// The display tree of the current Project as the Explorer derives it, from the store's cache
// (the Explorer has read it by the time a Group / Flow can be opened). Null before that.
function explorerProject(pid: string) {
  const nodes = projectStore.getCachedGroupTree(pid)
  return nodes ? buildExplorerProject(nodes, projectNodeId(pid)) : null
}

// The Group's display name for the recent list. `known` is the name the caller already holds
// (the create dialog's Group select); otherwise the cached tree, and when that cache is empty
// (a create has just invalidated it, or the tree is not read yet) the name recorded before,
// so a recent entry is never overwritten with the raw `module:<project>:<key>` id.
function groupNameOf(pid: string, groupId: string, known?: string): string {
  if (known) return known
  return (
    explorerProject(pid)?.groups.find((group) => group.id === groupId)?.name ??
    projectStore.recentGroups.find((group) => group.id === groupId && group.projectId === pid)?.name ??
    groupId
  )
}

// Default Group for 새 Flow: the Group chosen in the Explorer, else the active Flow's Group.
const flowCreateGroupId = computed<string | null>(() => {
  const pid = projectId.value
  if (!pid) return null
  const selected = explorerSelection.value
  if (selected?.kind === 'group') return selected.id
  const flowId = activeFlowRef.value?.groupId ?? null
  if (flowId === null) return null
  return explorerProject(pid)?.groups.find((group) => group.flows.some((flow) => flow.id === flowId))?.id ?? null
})

// Every way of opening a Flow ends here — the Explorer's double click / Enter (`reveal` false:
// the Explorer already shows that selection), Flow 열기…, 최근 Flow and a new Flow (`groupName`:
// the name the create dialog showed, since the tree cache is being re-read at that moment). The
// Center session ignores an activation for another Project.
function openFlow(payload: ProjectExplorerFlowEvent, reveal: boolean, groupName?: string) {
  center.openFlow(payload)
  projectStore.recordRecentFlow({ id: payload.flowId, name: payload.flowName, projectId: payload.projectId, groupId: payload.groupId })
  projectStore.recordRecentGroup({ id: payload.groupId, name: groupNameOf(payload.projectId, payload.groupId, groupName), projectId: payload.projectId })
  if (reveal) requestExplorer({ select: { kind: 'flow', id: payload.flowId }, expand: [payload.groupId] })
}

function onActivateFlow(payload: ProjectExplorerFlowEvent) {
  openFlow(payload, false)
}

// Opening a Group: it is selected in the Project Explorer and its Flow list unfolded.
function openGroup(group: { id: string; name: string }, pid: string) {
  projectStore.recordRecentGroup({ id: group.id, name: group.name, projectId: pid })
  requestExplorer({ select: { kind: 'group', id: group.id }, expand: [group.id] })
}

function onGroupChosen(group: ExplorerGroup) {
  dialog.value = null
  const pid = projectId.value
  if (pid) openGroup(group, pid)
}

function onFlowChosen(flow: ExplorerFlow) {
  dialog.value = null
  const pid = projectId.value
  if (pid) openFlow({ projectId: pid, groupId: flow.groupId, flowId: flow.id, flowName: flow.name }, true)
}

function onGroupCreated(created: CreatedGroup) {
  dialog.value = null
  const pid = projectId.value
  if (!pid) return
  explorerRefreshToken.value += 1
  openGroup({ id: created.id, name: created.title }, pid)
}

function onFlowCreated(created: DesignerCreatedFlow) {
  dialog.value = null
  const pid = projectId.value
  if (!pid) return
  explorerRefreshToken.value += 1
  openFlow({ projectId: pid, groupId: created.groupId, flowId: created.id, flowName: created.title }, true, created.groupName)
}

function onOpenRecent(request: DesignerRecentRequest) {
  if (request.kind === 'project') {
    const entry = projectStore.recentProjects.find((p) => p.id === request.id)
    if (entry) projectStore.openRecentProject(entry)
    return
  }
  const pid = projectId.value
  if (!pid) return
  if (request.kind === 'group') {
    const entry = projectStore.recentGroups.find((g) => g.id === request.id && g.projectId === pid)
    if (entry) openGroup(entry, pid)
    return
  }
  const entry = projectStore.recentFlows.find((f) => f.id === request.id && f.projectId === pid)
  if (entry) openFlow({ projectId: pid, groupId: entry.groupId, flowId: entry.id, flowName: entry.name }, true)
}

function onTopCommand(command: DesignerTopCommand) {
  // The parts disable what has no target; this is the same rule for a command that still arrives.
  if (!isMenuCommandEnabled(command, top.runMode, menuTargets.value)) return
  switch (command) {
    case 'project.new':
      projectStore.openDialog({ create: true })
      return
    case 'project.open':
      projectStore.openDialog()
      return
    case 'project.close':
      projectStore.closeCurrentProject()
      return
    case 'group.new':
      dialog.value = 'group-new'
      return
    case 'group.open':
      dialog.value = 'group-open'
      return
    case 'group.close': {
      const selected = explorerSelection.value
      if (selected?.kind === 'group') requestExplorer({ select: null, collapse: [selected.id] })
      return
    }
    case 'flow.new':
      dialog.value = 'flow-new'
      return
    case 'flow.open':
      dialog.value = 'flow-open'
      return
    case 'flow.close': {
      const flowId = center.state.activeFlowId
      if (flowId !== null) center.closeFlow(flowId)
      return
    }
    case 'designer.close':
      emit('close-designer')
      return
    default:
      top.runMode = nextRunMode(top.runMode, command)
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
