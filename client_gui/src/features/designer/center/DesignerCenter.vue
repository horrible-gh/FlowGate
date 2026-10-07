<template>
  <!-- flowgate.v02.0026 — Designer Center (0003-D §2.2): Flow Tabs over the Canvas area. The
       Canvas area shows exactly one of: Graph loading / Graph error / Canvas, and stays empty
       (no guidance text — flowgate.v02.0042) while no Flow is open.
       User actions become Center session actions; this part knows nothing about Vue Flow. -->
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
          @select-node="center.selectNode(activeTab.flowId, $event)"
          @viewport-change="center.recordViewport(activeTab.flowId, $event)"
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

const props = defineProps<{
  center: DesignerCenterSession
}>()

const { t } = useI18n()

const activeTab = props.center.activeTab
const load = props.center.activeGraphLoad
const view = props.center.activeView
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
