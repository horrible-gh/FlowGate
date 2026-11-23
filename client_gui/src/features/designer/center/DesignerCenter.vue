<template>
  <!-- Designer Center: Flow Tabs over the Canvas area. The
       Canvas area shows exactly one of: Graph loading / Graph error / Canvas, and stays empty
       (no guidance text — the earlier change) while no Flow is open.
       User actions become Center session actions; this part knows nothing about Vue Flow.
       S3+S4: the Canvas's structure edits go to the session's draft actions.
       flowgate.v02.0045 TR0020 rev2: 되돌리기 / 검증 / 실행 are Toolbar tools. TR0020 rev3: no
       편집 / 편집 마치기 — a Flow that already ran is edited right on the Canvas (the session picks
       what it shows). the earlier implementation: nothing is laid over the Canvas — no saving / refused-save /
       검증 note (the edit note is gone); a refused save reads the Flow again by itself, and the
       problems a 검증 or a refused 실행 reported only mark their cards / lines on the Canvas.
       flowgate.v02.0059 T#1: the session's [선택] mode and the Flow's whole Selection go to the
       Canvas, and the Selection it reports in that mode comes back to the session. the earlier change the earlier implementation: a
       drop of one card or of the selection goes to the session as one move (one undo entry).
       the density's move step (`move-step`, the Shell's) goes to the Canvas.
       flowgate.v02.0056 F2-1: a card's [승인] goes to the session (approveNode); its refusal is the run band's toast.
       the Canvas is editable only while the Flow's run does not lock it (the session's run marks:
       running / paused / stopping); the marks also draw the lock note, the protected lines and the running lines. A
       card's 병합 충돌 asks the Shell for the Git tool panel (`show-tool`), where the conflict session is resolved.
       flowgate.v02.0061 T#15: a card's context menu is the session's (cardMenu / chooseCardMenu: Hold 설정 / 해제,
       되돌리기), and the Graph Time Machine's confirmation and outcome are the session's dialog (timeMachine).
       (B1): a card title is no longer edited on the Canvas — the Inspector header saves it through the
       session (saveNodeTitle, wired by the Center column).
       the tabs also show their Flow's archive state (the last answered one, so a re-read does not
       flicker) and the run / chat ends seen while another tab was active (useFlowTabMarks: per Project in the browser,
       cleared when the tab is activated or closed). -->
  <div class="designer-center" data-test="designer-center">
    <FlowTabStrip
      :tabs="center.state.tabs"
      :active-flow-id="center.state.activeFlowId"
      :run-states="tabRunStates"
      :chat-states="tabChatStates"
      :terminal-states="flowTerminalUi.openFlows"
      :archived="tabArchived"
      :run-ends="tabRunEnds"
      :chat-ends="tabChatEnds"
      :conflicts="tabConflictMarks"
      :git-activity="gitTabActivity"
      @activate="center.activateFlow"
      @close="center.closeFlow"
      @reorder="center.moveFlow"
    />
    <div class="designer-center-canvas-area">
      <template v-if="activeTab">
        <LoadingState
          v-if="!load || load.status === 'loading'"
          :label="t('gui.designer.center.graph_loading')"
          data-test="designer-canvas-loading"
        />
        <ErrorState
          v-else-if="load.status === 'error'"
          :title="t('gui.designer.center.graph_load_failed')"
          :message="load.message || undefined"
          data-test="designer-canvas-error"
        >
          <template #action>
            <Button size="sm" data-test="designer-canvas-retry" @click="center.retryGraph(activeTab.flowId)">
              {{ t('gui.designer.center.retry') }}
            </Button>
          </template>
        </ErrorState>
        <FlowCanvas
          v-else
          :key="activeTab.flowId"
          :flow-id="activeTab.flowId"
          :flow-name="activeTab.name"
          :graph="load.graph"
          :viewport="view?.viewport ?? null"
          :selected-node-id="view?.selectedNodeId ?? null"
          :selected-node-ids="view?.selectedNodeIds ?? []"
          :select-mode="center.selectMode.value"
          :view-request="center.viewRequest.value?.flowId === activeTab.flowId ? center.viewRequest.value : null"
          :sample="center.isSampleSource"
          :placing="placing"
          :move-step="moveStep"
          @select-node="center.selectNode(activeTab.flowId, $event)"
          @select-nodes="center.selectNodes(activeTab.flowId, $event)"
          @viewport-change="center.recordViewport(activeTab.flowId, $event)"
          @viewport-commit="center.commitViewport(activeTab.flowId, $event)"
          @canvas-resize="center.recordCanvasSize($event)"
          @node-sizes="center.recordNodeSizes(activeTab.flowId, $event)"
          @nodes-move="center.moveNodes(activeTab.flowId, $event)"
          :editable="load.graph.draft?.editable === true && marks.lock === null"
          :marks="marks"
          :can-connect="(source: string, target: string) => center.checkConnection(activeTab!.flowId, source, target).ok"
          :issues="edit?.check?.issues ?? []"
          @canvas-drop="center.dropOnCanvas(activeTab.flowId, $event.transfer, $event.x, $event.y)"
          @canvas-place="emit('canvas-place', activeTab.flowId, $event.x, $event.y)"
          @cancel-placement="emit('cancel-placement')"
          @node-connect="center.connectNodes(activeTab.flowId, $event.source, $event.target)"
          @node-remove="center.removeNode(activeTab.flowId, $event)"
          @edge-remove="center.removeEdge(activeTab.flowId, $event)"
          @undo="center.undoEdit(activeTab.flowId)"
          :can-approve="center.canApproveFlow(activeTab.flowId)"
          :approving-node-id="center.activeRun.value?.approving ?? null"
          @open-document="emit('open-document', activeTab.flowId, $event)"
          @node-approve="center.approveNode(activeTab.flowId, $event)"
          @node-cancel="(nodeId, attemptId) => activeTab && center.cancelGateNode(activeTab.flowId, nodeId, attemptId)"
          @node-conflict="openCardMerge(activeTab.flowId, $event)"
          :card-menu="cardMenuOf"
          :holds="holds"
          @node-menu="chooseCardMenu"
        />
        <DraftActionNotice :notice="edit?.notice ?? null" />
        <RevisionNotice v-if="load?.status === 'ready'" :graph="load.graph" />
      </template>
    </div>
    <GraphTimeMachineDialog
      :dialog="timeMachine"
      :titles="timeMachineTitles"
      @confirm="center.confirmTimeMachine()"
      @close="center.closeTimeMachine()"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, watch, watchEffect, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../components/ui/Button.vue'
import ErrorState from '../../../components/ui/ErrorState.vue'
import LoadingState from '../../../components/ui/LoadingState.vue'
import FlowCanvas from './canvas/FlowCanvas.vue'
import type { DesignerCenterSession, FlowCardMenuKey } from './designerCenterSession'
import { useFlowChatActivity } from '@/features/conversation/flowChatActivity'
import FlowTabStrip, { type FlowTabRunState } from './FlowTabStrip.vue'
import { flowTerminalUi } from '../flowTerminal/state'
import { RUN_LOCKED_STATUSES } from './run/flowRunLock'
import { useFlowTabConflicts } from './flowTabConflicts'
import { useFlowTabMarks, type FlowTabChatEnd, type FlowTabRunEnd, type FlowTabRunFacts } from './flowTabMarks'
import { clearGitTabResult, gitTabActivity, pruneGitTabActivity, observeGitTabActivity } from './gitTabActivity'
import { cardNodeRun } from './graph/cardStateView'
import type { FlowGraphNodeRun } from './graph/flowGraph'
import { gateHeaderState } from '../inspector/features/gateCard/gateHeaderModel'
import RevisionNotice from './RevisionNotice.vue'
import DraftActionNotice from './edit/DraftActionNotice.vue'
import GraphTimeMachineDialog from './run/GraphTimeMachineDialog.vue'

const props = withDefaults(defineProps<{
  center: DesignerCenterSession
  placing?: boolean
  /** the step a card drag moves in; null = freeform. */
  moveStep?: number | null
}>(), { placing: false, moveStep: null })

const emit = defineEmits<{
  'canvas-place': [flowId: string, x: number, y: number]
  'cancel-placement': []
  /** a card's merge conflict — the Git tool panel resolves it. */
  'show-tool': [tool: 'git']
  'open-document': [flowId: string, document: import('./canvas/cardDocumentButtons').CardDocumentOpen]
}>()

const { t } = useI18n()

const activeTab = props.center.activeTab
const load = props.center.activeGraphLoad
const view = props.center.activeView
const edit = props.center.activeEdit
const marks = props.center.activeRunMarks
/** the active Flow's card context menu (the session's rules) and the item chosen in it. */
function cardMenuOf(nodeId: string) {
  return activeTab.value ? props.center.cardMenu(activeTab.value.flowId, nodeId) : []
}

/**
 * flowgate.v02test.0042 (mockup v7): a card's 병합 충돌 opens that card's '병합 · 충돌' Inspector tab — the one window event every
 * "open this card's merge" goes through (the Center column selects the card and asks the Inspector for the tab).
 */
function openCardMerge(flowId: string, nodeKey: string) {
  window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId, nodeKey } }))
}

function chooseCardMenu(nodeId: string, key: FlowCardMenuKey) {
  if (activeTab.value) props.center.chooseCardMenu(activeTab.value.flowId, nodeId, key)
}

/** the active Flow's card Holds as the session judges them (the Graph read's for every card). */
const holds = computed(() => (activeTab.value ? props.center.cardHolds(activeTab.value.flowId) : new Map<string, boolean>()))
/**
 * every open tab's run mark — its Flow's run status when it is one the run lock names (running /
 * paused / stopping) and confirmed by a read; a status not confirmed yet shows no mark. Every opened tab reads its run
 * with its Graph, so inactive tabs are known too.
 */
/** how each Flow's last chat answer ended, as the finish event said (taken once by the marks). */
const chatFinishes: Record<string, FlowTabChatEnd> = {}
const tabChatStates = useFlowChatActivity(() => props.center.state.projectId, () => props.center.state.tabs.map(tab => tab.flowId),
  (flowId, failed) => { chatFinishes[flowId] = failed ? 'failed' : 'done' })
// Read the same current gate and integration facts as the Canvas, for every open Flow.
const tabConflicts = computed(() => {
  const conflicts: Record<string, boolean> = {}
  for (const tab of props.center.state.tabs) {
    const graph = props.center.state.graphs[tab.flowId]
    if (graph?.status !== 'ready') continue
    const marks = props.center.runMarks(tab.flowId)
    conflicts[tab.flowId] = graph.graph.nodes.some((node) => {
      const runNode = marks.nodeRuns.get(node.id)
      const current = cardNodeRun(node.nodeRun as FlowGraphNodeRun | null | undefined, runNode)
      if (node.definition?.nodeType === 'gate' && gateHeaderState(current?.gate, current).key === 'conflict_review') return true
      return (runNode && 'integrationStatus' in runNode ? runNode.integrationStatus : node.nodeRun?.integrationStatus) === 'conflict'
    })
  }
  return conflicts
})
/**
 * flowgate.v02test.0066 #36-1: what the tabs show — 병합 충돌 or AI 해결 중 · n/m — from the Git panel's own answers
 * (the Project's git/status rows, and the AI run of the tabs that have a conflict), for every open tab. The Graph's
 * answer above still decides a tab Git has no conflict row for.
 */
const tabConflictMarks = useFlowTabConflicts({
  projectId: () => props.center.state.projectId,
  flows: () => props.center.state.tabs.map((tab) => tab.flowId),
  graphConflicts: () => tabConflicts.value,
})
const tabRunStates = computed(() => {
  const states: Record<string, FlowTabRunState> = {}
  for (const tab of props.center.state.tabs) {
    const { run, known } = props.center.runStatus(tab.flowId)
    if (known && run && RUN_LOCKED_STATUSES.includes(run.status)) states[tab.flowId] = run.status as FlowTabRunState
  }
  return states
})
/**
 * each open tab's archive state — the last one answered or set, kept while it is read again (an
 * archive event locks every tab to null for a moment), so a tab never flickers. A Group archive does not touch it.
 */
const archiveShown = reactive<Record<string, boolean>>({})
watchEffect(() => {
  const open = new Set(props.center.state.tabs.map((tab) => tab.flowId))
  for (const flowId of Object.keys(archiveShown)) if (!open.has(flowId)) delete archiveShown[flowId]
  for (const flowId of open) {
    const archived = props.center.isFlowArchived?.(flowId) ?? null
    if (archived !== null) archiveShown[flowId] = archived
  }
})
const tabArchived = computed(() => ({ ...archiveShown }))

const tabMarks = useFlowTabMarks({
  projectId: () => props.center.state.projectId,
  tabs: () => props.center.state.tabs.map((tab) => tab.flowId),
  activeFlowId: () => props.center.state.activeFlowId,
  runs: () => {
    const runs: Record<string, FlowTabRunFacts> = {}
    for (const tab of props.center.state.tabs) {
      const { run, known } = props.center.runStatus(tab.flowId)
      runs[tab.flowId] = { known, status: run?.status ?? null, idleReason: run?.idleReason ?? null }
    }
    return runs
  },
  chats: () => tabChatStates.value,
  chatEnd: (flowId) => {
    const end = chatFinishes[flowId] ?? null
    delete chatFinishes[flowId]
    return end
  },
})
const tabRunEnds = computed(() => {
  const ends: Record<string, FlowTabRunEnd> = {}
  for (const [flowId, mark] of Object.entries(tabMarks.marks)) if (mark.run) ends[flowId] = mark.run
  return ends
})
const tabChatEnds = computed(() => {
  const ends: Record<string, FlowTabChatEnd> = {}
  for (const [flowId, mark] of Object.entries(tabMarks.marks)) if (mark.chat) ends[flowId] = mark.chat
  return ends
})
// flowgate.v02test.0068: a Git result mark stays on its tab until the tab is activated again (left and come back) or closed.
const stopGitTabObserver = observeGitTabActivity(() => props.center.state.tabs.map(tab => tab.flowId))
onUnmounted(stopGitTabObserver)
watch(() => props.center.state.activeFlowId, (flowId) => { if (flowId) clearGitTabResult(flowId) })
watch(() => props.center.state.tabs.map((tab) => tab.flowId).join('\n'), () => {
  pruneGitTabActivity(new Set(props.center.state.tabs.map((tab) => tab.flowId)))
})
const timeMachine = props.center.timeMachine
/** the dialog's Flow's card titles by node key (its shown Graph). */
const timeMachineTitles = computed(() => {
  const flowId = timeMachine.value?.flowId
  const load = flowId ? props.center.state.graphs[flowId] : undefined
  return new Map(load?.status === 'ready' ? load.graph.nodes.map((node) => [node.id, node.title] as const) : [])
})
</script>

<style scoped>
.designer-storage-error {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  margin: 0;
  padding: 8px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
  background: var(--fg2-bg);
  color: var(--fg2-danger, #c43b3b);
}

.designer-center {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* Remaining height, positioning context for the Canvas (which fills it). */
.designer-center-canvas-area {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
</style>
