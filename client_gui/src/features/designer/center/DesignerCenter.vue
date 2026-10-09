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
       problems a 검증 or a refused 실행 reported only mark their cards / lines on the Canvas. -->
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
          :sample="center.isSampleSource"
          :placing="placing"
          @select-node="center.selectNode(activeTab.flowId, $event)"
          @viewport-change="center.recordViewport(activeTab.flowId, $event)"
          @viewport-commit="center.commitViewport(activeTab.flowId, $event)"
          @canvas-resize="center.recordCanvasSize($event)"
          @node-move="center.moveNode(activeTab.flowId, $event.nodeId, $event.x, $event.y)"
          :editable="load.graph.draft?.editable === true"
          :can-connect="(source: string, target: string) => center.checkConnection(activeTab!.flowId, source, target).ok"
          :issues="edit?.check?.issues ?? []"
          @canvas-drop="center.dropOnCanvas(activeTab.flowId, $event.transfer, $event.x, $event.y)"
          @canvas-place="emit('canvas-place', activeTab.flowId, $event.x, $event.y)"
          @cancel-placement="emit('cancel-placement')"
          @node-connect="center.connectNodes(activeTab.flowId, $event.source, $event.target)"
          @node-remove="center.removeNode(activeTab.flowId, $event)"
          @edge-remove="center.removeEdge(activeTab.flowId, $event)"
          @undo="center.undoEdit(activeTab.flowId)"
        />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../components/ui/Button.vue'
import ErrorState from '../../../components/ui/ErrorState.vue'
import LoadingState from '../../../components/ui/LoadingState.vue'
import FlowCanvas from './canvas/FlowCanvas.vue'
import type { DesignerCenterSession } from './designerCenterSession'
import FlowTabStrip from './FlowTabStrip.vue'

const props = withDefaults(defineProps<{
  center: DesignerCenterSession
  placing?: boolean
}>(), { placing: false })

const emit = defineEmits<{
  'canvas-place': [flowId: string, x: number, y: number]
  'cancel-placement': []
}>()

const { t } = useI18n()

const activeTab = props.center.activeTab
const load = props.center.activeGraphLoad
const view = props.center.activeView
const edit = props.center.activeEdit
</script>

<style scoped>
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
