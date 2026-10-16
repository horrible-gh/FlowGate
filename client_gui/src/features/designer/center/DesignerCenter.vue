<template>
  <!-- flowgate.v02.0026 — Designer Center (0003-D §2.2): Flow Tabs over the Canvas area. The
       Canvas area shows exactly one of: Graph loading / Graph error / Canvas, and stays empty
       (no guidance text — flowgate.v02.0042) while no Flow is open.
       User actions become Center session actions; this part knows nothing about Vue Flow.
       flowgate.v02.0045 S3+S4: the Canvas's structure edits go to the session's draft actions.
       flowgate.v02.0045 TR0020 rev2: 되돌리기 / 검증 / 실행 are Toolbar tools. TR0020 rev3: no
       편집 / 편집 마치기 — a Flow that already ran is edited right on the Canvas (the session picks
       what it shows). TR0020 rev4: nothing is laid over the Canvas — no saving / refused-save /
       검증 note (the edit note is gone); a refused save reads the Flow again by itself, and the
       problems a 검증 or a refused 실행 reported only mark their cards / lines on the Canvas.
       flowgate.v02.0059 T#1: the session's [선택] mode and the Flow's whole Selection go to the
       Canvas, and the Selection it reports in that mode comes back to the session. flowgate.v02.0059 T#2: a
       drop of one card or of the selection goes to the session as one move (one undo entry).
       flowgate.v02.0059 0017-T: the density's move step (`move-step`, the Shell's) goes to the Canvas.
       flowgate.v02.0056 F2-1: a card's [승인] goes to the session (approveNode); its refusal is the run band's toast.
       flowgate.v02.0061 T#14: the Canvas is editable only while the Flow's run does not lock it (the session's run marks:
       running / paused / stopping); the marks also draw the lock note, the protected lines and the running lines. A
       card's 병합 충돌 asks the Shell for the Git tool panel (`show-tool`), where the conflict session is resolved.
       flowgate.v02.0061 T#15: a card's context menu is the session's (cardMenu / chooseCardMenu: Hold 설정 / 해제,
       되돌리기), and the Graph Time Machine's confirmation and outcome are the session's dialog (timeMachine). -->
  <div class="designer-center" data-test="designer-center">
    <FlowTabStrip
      :tabs="center.state.tabs"
      :active-flow-id="center.state.activeFlowId"
      @activate="center.activateFlow"
      @close="center.closeFlow"
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
          :can-approve="center.canApprove"
          :approving-node-id="center.activeRun.value?.approving ?? null"
          @node-approve="center.approveNode(activeTab.flowId, $event)"
          @node-conflict="emit('show-tool', 'git')"
          :card-menu="cardMenuOf"
          :holds="holds"
          @node-menu="chooseCardMenu"
        />
        <p
          v-if="edit?.status === 'error' && edit.errorCode === 'document_storage_unwritable'"
          class="designer-storage-error" role="alert" data-test="designer-storage-error"
        >{{ t('gui.designer.center.document_storage_unwritable') }}</p>
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
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../components/ui/Button.vue'
import ErrorState from '../../../components/ui/ErrorState.vue'
import LoadingState from '../../../components/ui/LoadingState.vue'
import FlowCanvas from './canvas/FlowCanvas.vue'
import type { DesignerCenterSession, FlowCardMenuKey } from './designerCenterSession'
import FlowTabStrip from './FlowTabStrip.vue'
import GraphTimeMachineDialog from './run/GraphTimeMachineDialog.vue'

const props = withDefaults(defineProps<{
  center: DesignerCenterSession
  placing?: boolean
  /** flowgate.v02.0059 0017-T: the step a card drag moves in; null = 자율형. */
  moveStep?: number | null
}>(), { placing: false, moveStep: null })

const emit = defineEmits<{
  'canvas-place': [flowId: string, x: number, y: number]
  'cancel-placement': []
  /** flowgate.v02.0061 T#14: a card's merge conflict — the Git tool panel resolves it. */
  'show-tool': [tool: 'git']
}>()

const { t } = useI18n()

const activeTab = props.center.activeTab
const load = props.center.activeGraphLoad
const view = props.center.activeView
const edit = props.center.activeEdit
const marks = props.center.activeRunMarks
/** flowgate.v02.0061 T#15: the active Flow's card context menu (the session's rules) and the item chosen in it. */
function cardMenuOf(nodeId: string) {
  return activeTab.value ? props.center.cardMenu(activeTab.value.flowId, nodeId) : []
}

function chooseCardMenu(nodeId: string, key: FlowCardMenuKey) {
  if (activeTab.value) props.center.chooseCardMenu(activeTab.value.flowId, nodeId, key)
}

/** flowgate.v02.0061 T#15: the active Flow's card Holds as the session judges them (the Graph read's for every card). */
const holds = computed(() => (activeTab.value ? props.center.cardHolds(activeTab.value.flowId) : new Map<string, boolean>()))
const timeMachine = props.center.timeMachine
/** flowgate.v02.0061 T#15: the dialog's Flow's card titles by node key (its shown Graph). */
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
