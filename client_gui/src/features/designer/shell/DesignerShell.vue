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
       Flow) are mounted here and act on the same Project context, Explorer and Center.
       flowgate.v02.0046: the left / right Dock widths and the bottom Dock height follow the
       Body / CenterColumn resize handles; the Shell clamps each drag into the layout bounds. -->
  <div class="designer-shell" data-test="designer-shell" @keydown.esc="selectedPalette = null">
    <DesignerTop
      :run-phase="runPhase"
      :density="top.density"
      :zoom="zoom"
      :window-state="windowState"
      :targets="menuTargets"
      :recent="recent"
      :can-validate="center.activeCanValidate.value"
      :select-mode="center.selectMode.value"
      :run-band="runBand"
      @command="onTopCommand"
      @toggle-panel="onTogglePanel"
      @show-tab="onShowTab"
      @update:density="onDensity"
      @update:zoom="onZoomSelect"
      @open-recent="onOpenRecent"
    />
    <DesignerBody
      :left-width="leftColumnWidth(layout)"
      :right-width="rightColumnWidth(layout)"
      :left-resizable="!layout.leftCollapsed"
      :right-resizable="!layout.rightCollapsed"
      @resize-left="(width, bodyWidth) => (layout.leftWidth = clampLeftWidth(layout, width, bodyWidth))"
      @resize-right="(width, bodyWidth) => (layout.rightWidth = clampRightWidth(layout, width, bodyWidth))"
    >
      <template #left>
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
      </template>
      <template #center>
        <DesignerCenterColumn
          :center="center"
          :bottom-height="layout.bottomHeight"
          :placing="selectedPalette !== null"
          :move-step="moveStep"
          @canvas-place="onCanvasPlace"
          @cancel-placement="selectedPalette = null"
          :unselected="!hasWorkTarget"
          @resize-bottom="(height, columnHeight) => (layout.bottomHeight = clampBottomHeight(layout, height, columnHeight))"
          @show-tool="onShowTab"
        />
      </template>
      <template #right>
        <DesignerRightDock
          :project-id="projectId"
          :active-flow="activeFlowRef"
          :tool="layout.activeRightTool"
          :collapsed="layout.rightCollapsed"
          :unselected="!hasWorkTarget"
          :selected-palette-id="selectedPalette?.itemId ?? null"
          :source-locked="sourceLocked"
          @update:tool="layout.activeRightTool = $event"
          @palette-select="selectedPalette = $event"
          @palette-drag-start="selectedPalette = null"
        />
      </template>
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
    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
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
//
// flowgate.v02.0053 T#9 — the run buttons run the Flow on the server. 다음 단계 / 전체 실행 / 중지 go to
// the Center session (center/run/flowRunControl.ts: start or resume, one step, stop); the buttons' state
// (`top.runMode`) and the Toolbar's run dot follow the run the session read back, never the click alone.
// A refused run shows one short toast (its reason in plain words, no code); nothing else is written.
//
// flowgate.v02.0059 T#2 (0011-CH contracts C / D) — 정렬 / 배치 acts on the open Flow's cards through the
// Center session: the six 정렬 / two 균등 분배 commands arrange its selected cards and 자동 배치 lays every
// movable card out by the server's auto placement rules, only when that command is chosen. Each is one entry
// of the Flow's undo / redo, as a card drag is.
//
// flowgate.v02.0059 0017-T — the density (넓게 / 보통 / 좁게 / 자율형) is how finely a card drag moves: the
// Toolbar radios and the View menu set the one value (`top.density`, screen state only), and its move step
// (DENSITY_MOVE_STEP) goes to the Canvas, which applies it to the drags that follow — one card or the dragged
// selection alike. Choosing a density moves no card, saves nothing, adds no undo entry, changes no zoom and
// neither starts nor stops 자동 배치.
//
// flowgate.v02.0056 F2-1: a refused Canvas [승인] is a toast as well (its code's sentence and the server's message;
// a refused document is named with why and the documents approved before it, and that toast stays until closed);
// a stopped run with cards approved meanwhile shows "멈춤 · 다음 카드 N개 대기" in the band.
//
// flowgate.v02.0061 T#14: the run buttons follow the open Flow's run phase (idle / running / paused / stopping) and
// each sends one command to the Center session — 다음 단계 / 전체 실행 (step / full), 일시정지, 재개, 중지; 재실행 is never
// available. The band shows the run's status, command, idle reason and executions. The 0061 refusals whose wording is
// the server's (graph_run_locked, graph_git_required, …) show the server's message in the same toast. While the Flow's
// run locks it, the Conversation's AI source access mode cannot change (`sourceLocked`, WP answer 2) — its messages
// and read use stay as they are; the Canvas / Inspector lock is the Center session's, a separate rule.
//
// flowgate.v02.0061 T#15: a card's Hold is written through the card settings (features/document) the Center source is
// given (it is read with the Graph); a refused Hold 설정 / 해제 is a toast as well (with the server's message: graph_run_locked,
// graph_hold_done_node, …).
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { documentRead, settingsNodeHoldWrite } from '@/features/document'
import { useProjectStore, type CreatedGroup } from '@/features/project'
import ToastHost, { type ToastHostItem } from '../../../components/ui/ToastHost.vue'
import { createDesignerCenterSession, type ArrangeKind, type CanvasNodeSpec, type FlowRunFailure } from '../center/designerCenterSession'
import { createServerFlowGraphSource } from '../center/graph/serverFlowGraphSource'
import DesignerFlowCreateDialog, { type DesignerCreatedFlow } from '../fileMenu/DesignerFlowCreateDialog.vue'
import DesignerGroupCreateDialog from '../fileMenu/DesignerGroupCreateDialog.vue'
import DesignerOpenDialog from '../fileMenu/DesignerOpenDialog.vue'
import { paletteNodeSpec, readPaletteDragData, PALETTE_DRAG_MIME, type PaletteItemDefinition } from '../palette/paletteCatalog'
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
  DENSITY_MOVE_STEP,
  arrangeSelectionMinimum,
  createDesignerTopState,
  isMenuCommandEnabled,
  nextRunPhase,
  runBandView,
  runPhaseOf,
  zoomPercent,
  type DesignerDensity,
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
import {
  clampBottomHeight,
  clampLeftWidth,
  clampRightWidth,
  createDesignerLayout,
  leftColumnWidth,
  rightColumnWidth,
} from './designerLayout'

const emit = defineEmits<{
  /** File › Designer 닫기: the App Shell leaves the Designer (this Shell owns no router). */
  'close-designer': []
}>()

const projectStore = useProjectStore()
const { t, te } = useI18n()

const layout = reactive(createDesignerLayout())

const top = reactive(createDesignerTopState())

const projectId = computed<string | null>(() => projectStore.currentProjectId)

// flowgate.v02.0045 S1: real Flow Graphs through the project store's Canvas loaders (the sample
// source stays the session default for tests and previews only).
// flowgate.v02.0045 S3 (D0004 §2.3 "Palette 연결"): a Palette card dropped on the Canvas becomes a
// node of the Flow's Graph draft. The Shell is the only part that knows both: it tells the Center
// session which node a drop means, so the Palette and the Canvas never know each other.
const center = createDesignerCenterSession({
  // flowgate.v02.0059 T#2: the document read lets 자동 배치 follow a v0.1 sequence root's supersede chain.
  // flowgate.v02.0061 T#15: the card Hold of the card context menu goes through the card settings.
  source: createServerFlowGraphSource(projectStore, documentRead, { write: settingsNodeHoldWrite }),
  readDrop: (transfer) => {
    const definition = readPaletteDragData(transfer)
    return definition ? paletteNodeSpec(definition) : null
  },
})

// The Project Explorer's Flow id is the v0.1 group id, which is exactly FlowRef.groupId of
// the File Explorer — no lookup or conversion needed.
const activeFlowRef = center.activeFlowRef

const hasWorkTarget = computed(() => activeFlowRef.value !== null)
const selectedPalette = ref<PaletteItemDefinition | null>(null)

function onCanvasPlace(flowId: string, x: number, y: number) {
  const definition = selectedPalette.value
  if (!definition) return
  const transfer = { getData: (type: string) => type === PALETTE_DRAG_MIME ? JSON.stringify(definition) : '' }
  if (center.dropOnCanvas(flowId, transfer, x, y)) selectedPalette.value = null
}

watch(() => center.state.activeFlowId, () => { selectedPalette.value = null })

// flowgate.v02.0053 T#9 / flowgate.v02.0061 T#14: the run buttons follow the server run of the open Flow (the Center
// session's): its phase decides which two show; while a command is answered every run button waits (`runBusy`).
// TR0031 rev1: the phase and the band follow the session's confirmed run status (the run read, else the Canvas read's);
// until it is confirmed no run command is available (`runUnknown`).
const runPhase = computed(() => {
  if (!center.canRun) return top.runPhase
  // Show the pending Stop immediately; the existing action guard prevents duplicate requests.
  if (center.activeRun.value?.action === 'stop') return 'stopping'
  return runPhaseOf(center.activeRunStatus.value.run?.status)
})
// The band: status, command, idle reason, executions and the cards waiting for the next command (O1 §4-2's place).
const runBand = computed(() => (hasWorkTarget.value ? runBandView(center.activeRunStatus.value.run) : null))
// WP answer 2: the Conversation's source access mode waits while the open Flow's run is locked — TR0031 rev1: and while
// its run status is not confirmed (the session's `unknown` lock). Messages, viewing, panning and zooming go on.
const sourceLocked = computed(() => hasWorkTarget.value && center.activeRunMarks.value.lock !== null)

const toasts = ref<ToastHostItem[]>([])
let toastSeq = 0

function dismissToast(id: string | number) {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

/**
 * flowgate.v02.0061 T#14: refusals whose wording is the server's (sent in the request's x-locale): their message as
 * sent, when it says something.
 */
const SERVER_WORDED_ERRORS: ReadonlySet<string> = new Set([
  'graph_run_locked',
  'graph_git_required',
  'graph_attempt_inactive',
  'graph_hold_done_node',
  'graph_time_machine_blocked',
  'graph_engine_owned',
])

/** A refused run in plain words: the server's message for its 0061 codes, else the code's own sentence or the common one. */
function runErrorText(error: FlowRunFailure): string {
  const { code } = error
  const message = error.message.trim()
  if (code && SERVER_WORDED_ERRORS.has(code) && message && message !== code) return message
  const key = code ? `gui.designer.top.run_errors.${code}` : ''
  return key && te(key) ? t(key) : t('gui.designer.top.run_errors.other')
}

/**
 * flowgate.v02.0056 F2-1: a refused Canvas [승인] — the code's sentence (cardStateView grouping), then the
 * server's own message as it sent it (x-locale) when it says more, then what the refusal names: the refused
 * document, why, and the documents approved before it (document_approval_refused, O1 4-1).
 */
function approvalErrorText(error: FlowRunFailure): string {
  const head = t(error.approval?.labelKey ?? 'gui.designer.center.approval.failure.other')
  const message = error.message.trim()
  const parts = [message && message !== error.code && message !== head ? `${head} — ${message}` : head]
  const refusal = error.refusal
  if (refusal?.docId) parts.push(t('gui.designer.center.approval.refused_document', { doc: refusal.docId }))
  if (refusal?.reason) parts.push(t('gui.designer.center.approval.refused_reason', { reason: refusal.reason }))
  if (refusal?.approvedDocIds.length) parts.push(t('gui.designer.center.approval.approved_documents_named', { docs: refusal.approvedDocIds.join(', ') }))
  return parts.join(' · ')
}

/** flowgate.v02.0061 T#15: a refused Hold 설정 / 해제 — the common sentence and the server's own message when it says more. */
function holdErrorText(error: FlowRunFailure): string {
  const head = t('gui.designer.center.card_menu.hold_failed')
  const message = error.message.trim()
  if (message && message !== error.code) return `${head} — ${message}`
  const key = error.code ? `gui.designer.top.run_errors.${error.code}` : ''
  return key && te(key) ? `${head} — ${t(key)}` : head
}

watch(
  () => center.activeRun.value?.error?.seq ?? 0,
  (seq) => {
    const error = center.activeRun.value?.error
    if (!seq || !error) return
    toastSeq += 1
    const message = error.kind === 'approval' ? approvalErrorText(error) : error.kind === 'hold' ? holdErrorText(error) : runErrorText(error)
    const tone = error.kind === 'approval' && error.approval?.keepButton ? 'warning' : 'danger'
    // A refused document is read, not glanced at: that toast stays until it is closed.
    const duration = error.kind === 'approval' && error.approval?.kind === 'refused' ? null : 4000
    toasts.value = [...toasts.value.slice(-2), { id: `run-${toastSeq}`, message, tone, duration }]
  },
)

// The run buttons' UI state belongs to a target; it does not outlive it.
watch(hasWorkTarget, (has) => {
  if (!has) {
    top.runPhase = 'idle'
    selectedPalette.value = null
  }
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

// flowgate.v02.0045: send pending Canvas saves and close the Canvas change stream with the Shell.
onBeforeUnmount(() => center.dispose())

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
  // flowgate.v02.0059 T#2: the open Flow's structure edit history (Toolbar buttons and Edit menu); flowgate.v02.0061
  // T#14: it waits while the Flow's run locks the structure.
  canUndo: center.activeEdit.value?.canUndo === true && center.activeRunMarks.value.lock === null,
  canRedo: center.activeEdit.value?.canRedo === true && center.activeRunMarks.value.lock === null,
  // flowgate.v02.0061 T#14: a run command of the open Flow is being answered.
  runBusy: !!center.activeRun.value?.action,
  // flowgate.v02.0061 TR0031 rev1: the open Flow's run status is not confirmed yet (being read, or the read failed).
  runUnknown: center.canRun && !center.activeRunStatus.value.known,
  // flowgate.v02.0059 T#2: 정렬 (2+) / 균등 분배 (3+) need that many selected cards.
  selectedCount: center.activeView.value?.selectedNodeIds.length ?? 0,
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
function openFlow(payload: ProjectExplorerFlowEvent, reveal: boolean, groupName?: string, seedNode?: CanvasNodeSpec) {
  center.openFlow(payload, seedNode ? { seedNode } : undefined)
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
  openFlow(
    { projectId: pid, groupId: created.groupId, flowId: created.id, flowName: created.title },
    true,
    created.groupName,
    defaultFlowNode(),
  )
}

// flowgate.v02.0045 T0019: a new Flow starts with one 요구사항 node on its Canvas (a document node of
// the root type R, L0009 §2.5.1), added to its Graph draft by the Center session.
function defaultFlowNode(): CanvasNodeSpec {
  return {
    nodeType: 'document',
    docType: 'R',
    paletteItemId: null,
    label: t('gui.designer.center.node_kinds.requirement'),
    kind: 'requirement',
  }
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

/** Toolbar zoom preset (percent) for the open Flow's Canvas. */
function onZoomSelect(percent: number) {
  const flowId = center.state.activeFlowId
  if (flowId === null || !isMenuCommandEnabled('view.zoom-reset', runPhase.value, menuTargets.value)) return
  center.requestView(flowId, { kind: 'zoom-to', zoom: percent / 100 })
}

// flowgate.v02.0059 0017-T: the Toolbar radios and the View menu choose the one density. It only sets the step
// later card drags move in; nothing on the Canvas changes when it is chosen.
function onDensity(level: DesignerDensity) {
  top.density = level
}

/** flowgate.v02.0059 0017-T: the step a card drag moves in at the chosen density (null = 자율형, none). */
const moveStep = computed(() => DENSITY_MOVE_STEP[top.density])

function onTopCommand(command: DesignerTopCommand) {
  // The parts disable what has no target; this is the same rule for a command that still arrives.
  if (!isMenuCommandEnabled(command, runPhase.value, menuTargets.value)) return
  // flowgate.v02.0059 T#2: 정렬 / 균등 분배 of the open Flow's selected cards (the command names the kind).
  if (arrangeSelectionMinimum(command) !== null) {
    const flowId = center.state.activeFlowId
    if (flowId !== null) center.arrangeNodes(flowId, command.slice('edit.'.length) as ArrangeKind)
    return
  }
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
    // flowgate.v02.0045 TR0020 rev2: 되돌리기 / 검증 act on the open Flow's edited structure, and
    // 실행 takes that structure — it is confirmed first and the run starts only if that succeeds.
    case 'edit.undo': {
      const flowId = center.state.activeFlowId
      if (flowId !== null) center.undoEdit(flowId)
      return
    }
    // flowgate.v02.0059 T#2: 다시 실행 (Redo of an undone edit) — not the Run menu's 재실행 (run.rerun).
    case 'edit.redo': {
      const flowId = center.state.activeFlowId
      if (flowId !== null) center.redoEdit(flowId)
      return
    }
    // flowgate.v02.0059 T#1 (0011-CH contract A): [선택] toggles the Center session's selection mode;
    // the Edit menu's 전체 선택 selects every card of the open Flow in that mode (same Selection).
    case 'edit.select-mode':
      center.setSelectMode(!center.selectMode.value)
      return
    case 'edit.select-all': {
      const flowId = center.state.activeFlowId
      if (flowId !== null) center.selectAll(flowId)
      return
    }
    // flowgate.v02.0059 T#2: 자동 배치 of the open Flow by the server's rules and spacing (one undo entry).
    // 0017-T: the density has no part in it.
    case 'edit.auto-layout': {
      const flowId = center.state.activeFlowId
      if (flowId !== null) center.autoLayout(flowId)
      return
    }
    // flowgate.v02.0059 T#1: the Toolbar and the View menu emit the same command; the Canvas of the
    // open Flow does the fit / zoom and reports its Viewport, which the Toolbar's zoom value follows.
    case 'view.fit':
    case 'view.zoom-in':
    case 'view.zoom-out':
    case 'view.zoom-reset': {
      const flowId = center.state.activeFlowId
      if (flowId === null) return
      if (command === 'view.fit') center.requestView(flowId, { kind: 'fit' })
      else if (command === 'view.zoom-in') center.requestView(flowId, { kind: 'zoom-in' })
      else if (command === 'view.zoom-out') center.requestView(flowId, { kind: 'zoom-out' })
      else center.requestView(flowId, { kind: 'zoom-to', zoom: 1 })
      return
    }
    case 'run.validate': {
      const flowId = center.state.activeFlowId
      if (flowId !== null && center.activeCanValidate.value) void center.validateDraft(flowId)
      return
    }
    case 'run.next':
    case 'run.all': {
      const flowId = center.state.activeFlowId
      if (flowId === null) return
      // flowgate.v02.0061 T#14: one step / full command on the server (the session confirms an idle Flow's draft first).
      if (center.canRun) {
        void center.runFlow(flowId, command === 'run.next' ? 'next' : 'all')
        return
      }
      const start = (go: boolean) => {
        if (go && top.runPhase === 'idle') top.runPhase = nextRunPhase(top.runPhase, command)
      }
      const ready = center.prepareRun(flowId)
      if (typeof ready === 'boolean') start(ready)
      else void ready.then(start)
      return
    }
    // flowgate.v02.0061 T#14: 일시정지 / 재개 / 중지 — one server call each (T#2 /pause, /resume, T#7 /stop).
    case 'run.pause':
    case 'run.resume':
    case 'run.stop': {
      const flowId = center.state.activeFlowId
      if (flowId !== null && center.canRun) {
        if (command === 'run.pause') void center.pauseRun(flowId)
        else if (command === 'run.resume') void center.resumeRun(flowId)
        else void center.stopRun(flowId)
      } else top.runPhase = nextRunPhase(top.runPhase, command)
      return
    }
    default:
      top.runPhase = nextRunPhase(top.runPhase, command)
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
