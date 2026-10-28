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
       Body / CenterColumn resize handles; the Shell clamps each drag into the layout bounds.
       flowgate.v02.0060 T#2: the one Designer context menu (contextMenu/) is installed here — its host and view, and the
       connectors the five areas reach through inject; a chosen command runs through the paths below.
       flowgate.v02.0060 T#2 (2-B): the windows of its 11 existing server commands are mounted here too (the connector's). -->
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
          :file-refresh-token="fileRefreshToken"
          :restore-error="restoreError"
          @update:mode="layout.activeLeftMode = $event"
          @collapse="layout.leftCollapsed = true"
          @expand="layout.leftCollapsed = false"
          @activate-flow="onActivateFlow"
          @update:selection="explorerSelection = $event"
          @update:expanded="explorerExpanded = $event"
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
      :group-id="flowCreateGroup ?? flowCreateGroupId"
      @close="dialog = null"
      @created="onFlowCreated"
    />
    <FlowBaseBranchDialog :open="flowBaseTarget !== null" :project-id="projectId"
      :group-id="flowBaseTarget?.flowId ?? null" @close="flowBaseTarget = null" @saved="explorerRefreshToken += 1" />
    <DesignerContextMenu :host="contextMenu.host" @request="contextMenu.run" />
    <component :is="contextMenu.dialogs" v-if="contextMenu.actions" :actions="contextMenu.actions" />
    <ToastHost :toasts="shownToasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
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
// 0068 T0011: direction commands and a notice when candidate validation rejects a layout.
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
//
// flowgate.v02.0066 T#6 — the Center Diff (0066 D#2 §2-4, §3-10) the Shell owned and the Workspace drew over the Flow
// tabs and Canvas. flowgate.v02.0067 T#4 moved it into the Git part's own modeless (`Diff` tab, 0067-R §6): the Shell
// no longer receives a Git diff intent and the Center never shows one.
//
// flowgate.v02.0060 T#2 (0006-D rev2 2단계-A) — the Designer context menu: one host for the whole Designer and the
// connectors of the project tree, File Explorer, Flow tabs, Canvas and Palette cards (contextMenu/attach). A right
// click only reads state; a chosen command is checked again and runs through the path that already exists here —
// 새 Group = File › 새 Group, 새 플로우 = the 새 Flow dialog with the right-clicked Group given explicitly (the Explorer
// selection and the menu's default Group stay as they are), 전체 선택 = Edit › 전체 선택, 카드 추가 (Palette card) = the
// Palette selection the next Canvas click places. The Center session's commands are the connectors' own.
//
// flowgate.v02.0060 T#2 (0006-D rev2 §4.3 2-B) — the context menu's 11 existing server commands run through the
// features/project store (the v0.1 contracts; no new API): Group / Flow 이름 변경, Flow 삭제 = the existing dispose (DC),
// and the open Flow worktree's 새 폴더 / 새 파일 / 다운로드 / 업로드 / 삭제 / 수정 확인 (with the project Base read from the
// project's Git status). After a success the existing refresh paths show it: the Project Explorer re-reads its tree
// (refreshToken), the recent lists keep the new name (a disposed Flow leaves them), an open tab takes the new name
// through the Center's open path (the active tab stays), a disposed Flow's tab closes through the Center's close, and
// the File Explorer re-reads (its refreshToken). A refusal changes nothing; the window shows it.
//
// flowgate.v02.0105 T#1 (NR flowgate.v02.0101.0003 §5.4, Q #510 E-1) — an F5 brings the working screen back. See
// "F5 restore" below.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { documentRead, settingsNodeHoldWrite } from '@/features/document'
import { useProjectStore, type CreatedGroup, type DesignerSessionSnapshot, type DesignerSessionTab, type GroupTreeNode } from '@/features/project'
import ToastHost, { type ToastHostItem } from '../../../components/ui/ToastHost.vue'
import { createDesignerCenterSession, type ArrangeKind, type CanvasNodeSpec, type FlowRunFailure } from '../center/designerCenterSession'
import { createServerFlowGraphSource } from '../center/graph/serverFlowGraphSource'
import DesignerFlowCreateDialog, { type DesignerCreatedFlow } from '../fileMenu/DesignerFlowCreateDialog.vue'
import FlowBaseBranchDialog from '../fileMenu/FlowBaseBranchDialog.vue'
import DesignerGroupCreateDialog from '../fileMenu/DesignerGroupCreateDialog.vue'
import DesignerOpenDialog from '../fileMenu/DesignerOpenDialog.vue'
import { paletteNodeSpec, placedPaletteDefinition, readPaletteDragData, PALETTE_DRAG_MIME, type PaletteItemDefinition } from '../palette/paletteCatalog'
import DesignerContextMenu from '../contextMenu/DesignerContextMenu.vue'
import { useDesignerContextMenu, type DesignerMenuServerDeps, type MenuActionResult } from '../contextMenu/attach/designerMenuConnector'
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
  DESIGNER_ARRANGE_GROUPS,
  arrangeSelectionMinimum,
  autoLayoutDirectionOf,
  createDesignerTopState,
  isMenuCommandEnabled,
  nextRunPhase,
  runBandView,
  runPhaseOf,
  zoomPercent,
  type DesignerArrangeCommand,
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
  DESIGNER_LEFT_MODES,
  DESIGNER_RIGHT_TOOLS,
  clampBottomHeight,
  clampLeftWidth,
  clampRightWidth,
  createDesignerLayout,
  leftColumnWidth,
  rightColumnWidth,
  type DesignerLeftMode,
  type DesignerRightTool,
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
  // flowgate.v02.0088 F13: every placement (click, drag, context menu) passes here, so a default
  // card's label is made in the language of the placing moment, not of the selection.
  readDrop: (transfer) => {
    const definition = readPaletteDragData(transfer)
    return definition ? paletteNodeSpec(placedPaletteDefinition(definition, (key) => t(key))) : null
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

// flowgate.v02.0088 F16: a toast keeps what it says as meaning — the refusal, the notice, the menu's own words —
// and its sentence is built in the current language each time it renders, so a toast still on screen (a refused
// document's stays until it is closed) follows a language switch. The server's message and the document ids it
// names stay as they were sent.
interface ShellToast {
  readonly id: string
  readonly tone: NonNullable<ToastHostItem['tone']>
  readonly duration: number | null
  readonly text: () => string
}
const toasts = ref<ShellToast[]>([])
const shownToasts = computed<ToastHostItem[]>(() =>
  toasts.value.map((item) => ({ id: item.id, tone: item.tone, duration: item.duration, message: item.text() })),
)
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
    const text = () => (error.kind === 'approval' ? approvalErrorText(error) : error.kind === 'hold' ? holdErrorText(error) : runErrorText(error))
    const tone = error.kind === 'approval' && error.approval?.keepButton ? 'warning' : 'danger'
    // A refused document is read, not glanced at: that toast stays until it is closed.
    const duration = error.kind === 'approval' && error.approval?.kind === 'refused' ? null : 4000
    toasts.value = [...toasts.value.slice(-2), { id: `run-${toastSeq}`, text, tone, duration }]
  },
)

// A rejected candidate does not move cards; show its reason once.
watch(
  () => center.layoutNotice.value?.seq ?? 0,
  (seq) => {
    const notice = center.layoutNotice.value
    if (!seq || !notice) return
    const base = 'gui.designer.top.layout_notices'
    const reason = notice.reason
    toastSeq += 1
    toasts.value = [
      ...toasts.value.slice(-2),
      {
        id: `layout-${toastSeq}`,
        text: () => `${t(`${base}.invalid`)} ${t(`${base}.invalid_reasons.${reason}`)}`,
        tone: 'warning',
        duration: 6000,
      },
    ]
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
  // flowgate.v02.0105 T#1: the F5 restore, else the screen is saved from here on.
  startRestore()
  if (!restoring) {
    saving = true
    save()
  }
})

// flowgate.v02.0045: send pending Canvas saves and close the Canvas change stream with the Shell.
// flowgate.v02.0105 T#1: a waiting restore ends with the Shell; the snapshot stays for the next entry.
onBeforeUnmount(() => {
  endRestore()
  center.dispose()
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
const flowBaseTarget = ref<{ projectId: string; flowId: string } | null>(null)
watch(projectId, () => { flowBaseTarget.value = null }, { flush: 'sync' })

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
// flowgate.v02.0060 T#2: bumped after a context menu command changed the open Flow's worktree (the File Explorer re-reads).
const fileRefreshToken = ref(0)

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

// flowgate.v02.0060 T#2: the Group a context menu's 새 플로우 named, for that dialog only (null = the default above).
const flowCreateGroup = ref<string | null>(null)
watch(dialog, (next) => {
  if (next !== 'flow-new') flowCreateGroup.value = null
})

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
      projectStore.clearDesignerSession()
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
      // flowgate.v02.0105 T#1: an explicit close is not an F5 — the snapshot goes, and neither a waiting restore nor
      // a later change of this Shell writes it again.
      endRestore()
      closing = true
      projectStore.clearDesignerSession()
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
    // flowgate.v02.0059 T#2: 자동 배치 of the open Flow (one undo entry). 0017-T: the density has no part in it.
    // flowgate.v02.0068 T#2: 가로 / 세로 자동정렬 — the direction goes with the command only (spacing scale 1).
    case 'edit.auto-layout-horizontal':
    case 'edit.auto-layout-vertical': {
      const flowId = center.state.activeFlowId
      const direction = autoLayoutDirectionOf(command)
      if (flowId !== null && direction !== null) center.autoLayout(flowId, direction)
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

// --- F5 restore (flowgate.v02.0105 T#1, NR flowgate.v02.0101.0003 §5.4) -------------------------------------------
// The working screen of this browser tab — the Project, its Explorer Group context (the selected row and the open
// Groups), the Flow tabs in order, the active Flow and the left / right panel tabs — is the store's Designer snapshot.
// The next entry of this tab (an F5, or the Dashboard and back) brings it back while no other Project is current.
// The selected card, Inspector, run state and drafts are not part of it; a Canvas Viewport comes back from the
// server already.
//   · The restore reads the Project's complete tree (final-approved / discarded Groups included) before it opens
//     anything; a Flow or Group not in it (deleted, or another Project's) is left out. A tree read refused with
//     403 / 404 drops the snapshot and leaves the Designer without the Project it set; a passing failure (network,
//     5xx) keeps the Project and the snapshot as they are and is shown over the Explorer's tree with its retry; the
//     restore reads again once the Explorer has read the tree again (that retry, or its refresh).
//   · Flows open as the Explorer's activation opens them, without a default node or a recent entry. A restored tab
//     whose Graph read is refused with 403 / 404 closes (and so leaves the snapshot); a passing failure stays as an
//     error tab with its retry. The saved selection is set without unfolding its Group: the open Groups are as saved.
//   · Every step after an await checks that the restore is still the current one (`restoreSeq`, its Project, its
//     user): Project 닫기, Designer 닫기, another Project, a logout and leaving the Designer end it, so a late answer
//     neither changes the screen nor writes or drops a snapshot.
//   · What the user changed while it waited stays, even when changed back: an item the user touched (the tabs, the
//     active Flow, the Explorer selection / open Groups, a panel tab) is the user's until the restore ends, a Flow the
//     user closed is not opened again, and restored tabs go after the tabs the user opened.
//   · Saving starts when the restore ends, or at the user's first change while it waits; the saved items it has not
//     confirmed and the user has not changed are kept as `pending`, so the entry state never replaces them.

// The open Groups the Project Explorer reports (update:expanded), next to its selection above.
const explorerExpanded = ref<readonly string[]>([])

/** The restorable items of the screen, each as one comparable value. */
interface ScreenItems {
  readonly tabs: string
  readonly active: string | null
  readonly selection: string
  readonly expanded: string
  readonly left: DesignerLeftMode
  readonly right: DesignerRightTool
}

type ScreenItem = keyof ScreenItems

function screenItems(): ScreenItems {
  return {
    tabs: center.state.tabs.map((tab) => tab.flowId).join('\n'),
    active: center.state.activeFlowId,
    selection: JSON.stringify(explorerSelection.value),
    expanded: [...explorerExpanded.value].sort().join('\n'),
    left: layout.activeLeftMode,
    right: layout.activeRightTool,
  }
}

/** What a snapshot asks to bring back: its fields, with what an earlier restore had not confirmed taken over. */
interface RestoreTarget {
  readonly tabs: DesignerSessionTab[]
  readonly activeFlowId: string | null
  readonly selection: DesignerSessionSnapshot['explorer']['selection']
  readonly expandedGroupIds: string[]
  readonly leftMode: string
  readonly rightTool: string
}

function restoreTarget(saved: DesignerSessionSnapshot): RestoreTarget {
  const pending = saved.pending
  return {
    tabs: uniqueTabs([...saved.tabs, ...(pending?.tabs ?? [])]),
    activeFlowId: pending?.activeFlowId !== undefined ? pending.activeFlowId : saved.activeFlowId,
    selection: pending?.explorer.selection ?? saved.explorer.selection,
    expandedGroupIds: [...new Set([...saved.explorer.expandedGroupIds, ...(pending?.explorer.expandedGroupIds ?? [])])],
    leftMode: pending?.leftMode ?? saved.leftMode,
    rightTool: pending?.rightTool ?? saved.rightTool,
  }
}

interface RestoreRun {
  readonly seq: number
  readonly pid: string
  readonly owner: string | null
  readonly target: RestoreTarget
  /** True when the restore made the Project current (a refused tree takes it back). */
  readonly setProject: boolean
  /** The screen as last seen while the restore runs (the restore itself changes nothing before it ends). */
  seen: ScreenItems
  /** The items the user changed since the restore began — the user's even when changed back. */
  readonly touched: Set<ScreenItem>
  /** The Flows the user closed meanwhile (and did not open again): neither restored nor kept as pending. */
  readonly closed: Set<string>
  phase: 'reading' | 'pending'
  /** The Explorer's tree when the read failed; a newer one is the retry. */
  failedTree?: readonly GroupTreeNode[]
}

let restoreSeq = 0
let restoring: RestoreRun | null = null
let saving = false
let closing = false

// The failed tree read of a waiting restore ('' = no server message), shown by the Project Explorer with its retry.
const restoreError = ref<string | null>(null)

function endRestore() {
  restoreSeq += 1
  restoring = null
  restoreError.value = null
}

function restoreAlive(run: RestoreRun): boolean {
  return (
    !closing &&
    run === restoring &&
    run.seq === restoreSeq &&
    run.pid === projectStore.currentProjectId &&
    run.owner === projectStore.designerSessionOwner
  )
}

const tabIdsOf = (items: ScreenItems) => (items.tabs ? items.tabs.split('\n') : [])

// Every change seen while a restore runs is the user's, and stays the user's per item until the restore ends.
function noteUserChanges(run: RestoreRun) {
  const now = screenItems()
  for (const item of Object.keys(now) as ScreenItem[]) if (now[item] !== run.seen[item]) run.touched.add(item)
  const open = tabIdsOf(now)
  for (const flowId of tabIdsOf(run.seen)) if (!open.includes(flowId)) run.closed.add(flowId)
  for (const flowId of open) run.closed.delete(flowId)
  run.seen = now
}

function startRestore() {
  const saved = projectStore.readDesignerSession()
  const current = projectStore.currentProjectId
  if (!saved || (current !== null && current !== saved.projectId)) return
  if (current === null) projectStore.restoreCurrentProject(saved.projectId)
  // Directly, before the Project watch would: its setProject would clear tabs this restore has opened by then.
  center.setProject(saved.projectId)
  restoring = {
    seq: ++restoreSeq,
    pid: saved.projectId,
    owner: projectStore.designerSessionOwner,
    target: restoreTarget(saved),
    setProject: current === null,
    seen: screenItems(),
    touched: new Set(),
    closed: new Set(),
    phase: 'reading',
  }
  void readRestoreTree(restoring)
}

async function readRestoreTree(run: RestoreRun) {
  run.phase = 'reading'
  restoreError.value = null
  let nodes: GroupTreeNode[]
  try {
    nodes = await projectStore.fetchGroupTree(run.pid, { includeTerminal: true, force: true })
  } catch (error) {
    if (!restoreAlive(run)) return
    const { status } = projectStore.readFailureOf(error)
    if (status === 403 || status === 404) refuseRestore(run)
    else {
      run.phase = 'pending'
      run.failedTree = projectStore.getCachedGroupTree(run.pid)
      restoreError.value = error instanceof Error ? error.message.trim() : ''
    }
    return
  }
  // The Explorer takes the new Project (which clears its Group context) before that context is asked for.
  await nextTick()
  if (restoreAlive(run)) applyRestore(run, nodes)
}

// The Project itself is refused: its snapshot goes, and the Project this restore set with it.
function refuseRestore(run: RestoreRun) {
  endRestore()
  saving = true
  projectStore.clearDesignerSession()
  if (!run.setProject) return
  projectStore.closeCurrentProject()
  menuToast(() => t('gui.designer.project_explorer.load_failed'), 'warning')
}

// A passing failure: the Explorer's next successful tree read (its retry / refresh) is the restore's retry.
watch(
  () => projectStore.getCachedGroupTree(restoring?.pid ?? ''),
  (tree) => {
    const run = restoring
    if (run && run.phase === 'pending' && tree && tree !== run.failedTree && restoreAlive(run)) void readRestoreTree(run)
  },
)

function uniqueTabs(tabs: readonly DesignerSessionTab[]): DesignerSessionTab[] {
  const seen = new Set<string>()
  return tabs.filter((tab) => !seen.has(tab.flowId) && !!seen.add(tab.flowId))
}

function sameSelection(a: ExplorerSelection, b: ExplorerSelection): boolean {
  return a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id)
}

function applyRestore(run: RestoreRun, nodes: GroupTreeNode[]) {
  noteUserChanges(run)
  const { target, touched, closed, pid } = run
  // Ended first, so nothing the restore changes below is taken for the user's.
  endRestore()
  const groups = buildExplorerProject(nodes, projectNodeId(pid))?.groups ?? []
  const flows = new Map(groups.flatMap((group) => group.flows.map((flow) => [flow.id, flow] as const)))
  const groupIds = new Set(groups.map((group) => group.id))
  const userActive = center.state.activeFlowId
  const userLeft = layout.activeLeftMode

  // Flow tabs in their saved order, after the ones the user opened meanwhile; a Flow the user closed stays closed.
  const tabs = target.tabs.filter((tab) => flows.has(tab.flowId) && !closed.has(tab.flowId))
  for (const tab of tabs) {
    if (center.state.tabs.some((open) => open.flowId === tab.flowId)) continue
    center.openFlow({ projectId: pid, flowId: tab.flowId, flowName: flows.get(tab.flowId)!.name })
    graphChecks.value = [...graphChecks.value, { flowId: tab.flowId, pid }]
  }
  const userTabs = touched.has('tabs') || touched.has('active')
  const savedActive = tabs.some((tab) => tab.flowId === target.activeFlowId) ? target.activeFlowId : (center.state.tabs[0]?.flowId ?? null)
  const active = userTabs && userActive !== null ? userActive : savedActive
  if (active !== null) center.activateFlow(active)

  // The Group context, through the request the File menu uses (openGroup would record a recent Group). The selection
  // and the open Groups are each as saved, so a Group folded over its selected Flow stays folded.
  const selected = target.selection
  const inTree =
    selected === null
      ? false
      : selected.kind === 'project'
        ? selected.id === projectNodeId(pid)
        : selected.kind === 'group'
          ? groupIds.has(selected.id)
          : flows.has(selected.id)
  const select = touched.has('selection') ? explorerSelection.value : inTree ? selected : null
  const expand = touched.has('expanded') ? [] : target.expandedGroupIds.filter((id) => groupIds.has(id))
  if (expand.length > 0 || !sameSelection(select, explorerSelection.value)) requestExplorer({ select, expand, reveal: false })

  // The panel tabs after that request, which brings the Project Explorer to the front.
  const savedLeft = DESIGNER_LEFT_MODES.find((mode) => mode === target.leftMode)
  const savedRight = DESIGNER_RIGHT_TOOLS.find((tool) => tool === target.rightTool)
  layout.activeLeftMode = touched.has('left') ? userLeft : (savedLeft ?? userLeft)
  if (!touched.has('right') && savedRight) layout.activeRightTool = savedRight

  saving = true
  save()
}

// Restored tabs whose first Graph read has not answered yet. A refused one (403 / 404: no access any more, or removed
// after the tree read) closes; a passing failure stays with its retry. Only for the Project it was opened in.
const graphChecks = ref<{ flowId: string; pid: string }[]>([])

watch(
  () => graphChecks.value.map(({ flowId }) => center.state.graphs[flowId]?.status ?? null),
  () => {
    const waiting: { flowId: string; pid: string }[] = []
    for (const check of graphChecks.value) {
      const load = center.state.graphs[check.flowId]
      if (!load || center.state.projectId !== check.pid) continue
      if (load.status === 'loading') waiting.push(check)
      else if (load.status === 'error' && (load.httpStatus === 403 || load.httpStatus === 404)) center.closeFlow(check.flowId)
    }
    if (waiting.length !== graphChecks.value.length) graphChecks.value = waiting
  },
)

// Another Project, or none (Project 닫기): a waiting restore ends there, and what is shown from then on is saved.
watch(projectId, (id) => {
  graphChecks.value = graphChecks.value.filter((check) => check.pid === id)
  if (!restoring || restoring.pid === id) return
  endRestore()
  saving = true
  save()
})

// A logout (or another user) ends a waiting restore; nothing is kept while nobody is signed in.
watch(
  () => projectStore.designerSessionOwner,
  () => {
    if (restoring) endRestore()
  },
)

/** The Group of each Flow, from the trees of the Project read so far. */
function flowGroupIds(pid: string): Map<string, string> {
  const ids = new Map<string, string>()
  for (const includeTerminal of [true, false]) {
    const nodes = projectStore.getCachedGroupTree(pid, includeTerminal)
    for (const group of (nodes && buildExplorerProject(nodes, projectNodeId(pid))?.groups) || []) {
      for (const flow of group.flows) ids.set(flow.id, group.id)
    }
  }
  return ids
}

function save() {
  if (!saving || closing) return
  const pid = projectId.value
  if (pid === null) {
    projectStore.clearDesignerSession()
    return
  }
  const groupOf = flowGroupIds(pid)
  const selection = explorerSelection.value
  const snapshot: DesignerSessionSnapshot = {
    v: 1,
    projectId: pid,
    explorer: { selection: selection ? { kind: selection.kind, id: selection.id } : null, expandedGroupIds: [...explorerExpanded.value] },
    tabs: center.state.tabs.map((tab) => ({ flowId: tab.flowId, name: tab.name, groupId: groupOf.get(tab.flowId) ?? null })),
    activeFlowId: center.state.activeFlowId,
    leftMode: layout.activeLeftMode,
    rightTool: layout.activeRightTool,
  }
  // A restore still waiting: what it has not confirmed stays for the next entry, except what the user changed.
  const run = restoring
  if (run && run.pid === pid) {
    noteUserChanges(run)
    const { target, touched, closed } = run
    const pending: NonNullable<DesignerSessionSnapshot['pending']> = {
      tabs: target.tabs.filter((tab) => !closed.has(tab.flowId)),
      explorer: {
        selection: touched.has('selection') ? null : target.selection,
        expandedGroupIds: touched.has('expanded') ? [] : target.expandedGroupIds,
      },
    }
    if (!touched.has('tabs') && !touched.has('active')) pending.activeFlowId = target.activeFlowId
    if (!touched.has('left')) pending.leftMode = target.leftMode
    if (!touched.has('right')) pending.rightTool = target.rightTool
    snapshot.pending = pending
  }
  projectStore.writeDesignerSession(snapshot)
}

// Every change of a restorable item (tab names included) is saved; while a restore waits, only from the user's first
// change on.
watch(
  () => JSON.stringify([projectId.value, screenItems(), center.state.tabs.map((tab) => tab.name)]),
  () => {
    const run = restoring
    if (run && run.pid === projectId.value) {
      noteUserChanges(run)
      if (!saving && run.touched.size === 0) return
      saving = true
    }
    save()
  },
)

// flowgate.v02.0060 T#2: the Designer context menu and what its commands run through (see the head comment).
// flowgate.v02.0060 T#1 (WP 0012): its 정렬 · 배치 / 정렬 are this Edit menu's 정렬 / 배치 — the same commands, rule and path.
// flowgate.v02.0060 T#2: the 2-B server paths (see the head comment). Each answers a result; a refusal is the store's.
async function attempt<T>(work: () => Promise<T>): Promise<MenuActionResult<T>> {
  try {
    return { ok: true, value: await work() }
  } catch (error) {
    return { ok: false, refusal: projectStore.sourceRefusal(error) }
  }
}

function explorerGroup(pid: string, groupId: string) {
  return explorerProject(pid)?.groups.find((group) => group.id === groupId) ?? null
}

/** A renamed Flow's open tab takes the new name through the Center's open path; the active tab stays the active one. */
function renameFlowTab(pid: string, flowId: string, name: string) {
  if (!center.state.tabs.some((tab) => tab.flowId === flowId)) return
  const active = center.state.activeFlowId
  center.openFlow({ projectId: pid, flowId, flowName: name })
  if (active !== null && active !== flowId) center.activateFlow(active)
}

function menuToast(message: string | (() => string), tone: 'success' | 'warning' | 'danger') {
  toastSeq += 1
  const text = typeof message === 'function' ? message : () => message
  toasts.value = [...toasts.value.slice(-2), { id: `menu-${toastSeq}`, text, tone, duration: 4000 }]
}

const menuServer: DesignerMenuServerDeps = {
  group: (pid, groupId) => {
    const group = explorerGroup(pid, groupId)
    return group ? { name: group.name, moduleName: group.moduleName } : null
  },
  flow: (pid, flowId) => explorerProject(pid)?.groups.flatMap((group) => group.flows).find((flow) => flow.id === flowId)?.name ?? null,
  renameGroup: async (pid, groupId, title) => {
    const group = explorerGroup(pid, groupId)
    if (!group) return { ok: false, refusal: { reason: 'not_found', status: null, code: null } }
    const result = await attempt(() => projectStore.renameGroup(pid, group.moduleName, title))
    if (result.ok) {
      projectStore.renameRecent('group', groupId, title)
      explorerRefreshToken.value += 1
    }
    return result
  },
  renameFlow: async (pid, _groupId, flowId, title) => {
    const result = await attempt(() => projectStore.renameFlow(pid, flowId, title))
    if (result.ok) {
      projectStore.renameRecent('flow', flowId, title)
      renameFlowTab(pid, flowId, title)
      explorerRefreshToken.value += 1
    }
    return result
  },
  disposeFlow: async (pid, _groupId, flowId, reason) => {
    const result = await attempt(() => projectStore.disposeFlow(pid, flowId, reason))
    if (result.ok) {
      projectStore.forgetRecentFlow(flowId)
      // The disposed Flow's open tab and Canvas close through the Center's own close (its pending saves included).
      center.closeFlow(flowId)
      explorerRefreshToken.value += 1
    }
    return result
  },
  createEntry: async (pid, flowId, kind, parentPath, name) => {
    const result = await attempt(() => projectStore.createSourceEntry(pid, flowId, kind, parentPath, name))
    if (result.ok) fileRefreshToken.value += 1
    return result
  },
  download: (pid, flowId, kind, path, fallbackName) => attempt(() => projectStore.downloadSourceEntry(pid, flowId, kind, path, fallbackName)),
  // Only a hint for the upload window: the tree read leaves out ignored and unreadable files (the window always asks).
  fileNames: (pid, flowId, path) => attempt(async () => {
    const tree = await projectStore.loadFlowFileTree(pid, flowId)
    const prefix = path ? `${path}/` : ''
    return tree.nodes
      .filter((node) => node.type === 'file' && node.path.startsWith(prefix) && !node.path.slice(prefix.length).includes('/'))
      .map((node) => node.name)
  }),
  upload: async (pid, flowId, targetPath, files) => {
    const result = await attempt(() => projectStore.uploadSourceFiles(pid, flowId, targetPath, files))
    if (result.ok) fileRefreshToken.value += 1
    return result
  },
  remove: async (pid, flowId, kind, path) => {
    const result = await attempt(() => projectStore.deleteSourceEntry(pid, flowId, kind, path))
    if (result.ok) fileRefreshToken.value += 1
    return result
  },
  // The comparison standard is the project Base (as the File Explorer's change list): read, sent, and checked in the answer.
  fileDiff: async (pid, flowId, path) => {
    let base: string | null = null
    try {
      base = (await projectStore.loadProjectGitStatus(pid))?.baseBranch ?? null
    } catch {
      base = null
    }
    if (!base) return { ok: false, refusal: { reason: 'base_unavailable', status: null, code: null } }
    const compareBase = base
    const read = await attempt(() => projectStore.loadFlowFileDiff(pid, flowId, path, { base: compareBase }))
    if (!read.ok) return read
    if (read.value.baseBranch !== compareBase) return { ok: false, refusal: { reason: 'base_mismatch', status: null, code: null } }
    return { ok: true, value: { base: compareBase, status: read.value.status, old: read.value.old, new: read.value.new } }
  },
  notify: menuToast,
}

const isArrangeCommand = (command: string): command is DesignerArrangeCommand =>
  DESIGNER_ARRANGE_GROUPS.some((group) => (group as readonly string[]).includes(command))
const contextMenu = useDesignerContextMenu({
  center,
  projectId: () => projectId.value,
  canSelectAll: () => isMenuCommandEnabled('edit.select-all', runPhase.value, menuTargets.value),
  selectAll: (flowId) => {
    if (center.state.activeFlowId === flowId) onTopCommand('edit.select-all')
  },
  projectGitEnabled: (pid) => projectStore.knownGitEnabled?.[pid],
  openFlowBaseBranch: (pid, flowId) => {
    if (projectId.value === pid) flowBaseTarget.value = { projectId: pid, flowId }
  },
  openGroupCreate: () => onTopCommand('group.new'),
  openFlowCreate: (groupId) => {
    if (!projectId.value) return
    flowCreateGroup.value = groupId
    dialog.value = 'flow-new'
  },
  selectPalette: (definition) => {
    selectedPalette.value = definition
  },
  arrange: {
    groups: DESIGNER_ARRANGE_GROUPS,
    enabled: (command) => isArrangeCommand(command) && isMenuCommandEnabled(command, runPhase.value, menuTargets.value),
    run: (flowId, command) => {
      if (isArrangeCommand(command) && center.state.activeFlowId === flowId) onTopCommand(command)
    },
  },
  server: menuServer,
  layout: () => [layout.leftCollapsed, layout.rightCollapsed, layout.activeLeftMode, layout.activeRightTool],
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
