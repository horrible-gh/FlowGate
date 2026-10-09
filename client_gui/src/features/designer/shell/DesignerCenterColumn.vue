<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. The
       Designer Center session passes through to the Workspace; the Bottom Inspector
       (flowgate.v02.0036) gets the session's selected Node; flowgate.v02.0049: the Minimap gets the
       active Flow's ready Graph, Viewport and Canvas size, and its moves go back to the session.
       flowgate.v02.0039 T#3: `unselected` (no work target yet) passes to both.
       flowgate.v02.0046: a resize handle on the Workspace ↔ Bottom Dock line asks the Shell for
       a new Bottom Dock height; the Workspace (and its Canvas) takes the rest. -->
  <div ref="columnRef" class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :center="center" :unselected="unselected" />
    <DesignerResizeHandle
      class="designer-center-resize"
      axis="y"
      :size="bottomHeight"
      :grow="-1"
      data-test="designer-bottom-resize"
      @resize="emit('resize-bottom', $event, columnRef?.clientHeight ?? 0)"
    />
    <DesignerBottomDock
      :height="bottomHeight"
      :selected-node="center.activeSelectedNode.value"
      :unselected="unselected"
      :minimap="minimap"
      @minimap-viewport="onMiniMapViewport"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CanvasViewport } from '../center/canvasView'
import type { DesignerCenterSession } from '../center/designerCenterSession'
import type { FlowGraph } from '../center/graph/flowGraph'
import DesignerBottomDock from './DesignerBottomDock.vue'
import DesignerResizeHandle from './DesignerResizeHandle.vue'
import DesignerWorkspace from './DesignerWorkspace.vue'

const props = withDefaults(
  defineProps<{
    center: DesignerCenterSession
    bottomHeight: number
    unselected?: boolean
  }>(),
  { unselected: false },
)

// Each drag step carries the column's own height, which the Shell's clamp keeps the Workspace's room in.
const emit = defineEmits<{ 'resize-bottom': [height: number, columnHeight: number] }>()

const columnRef = ref<HTMLElement | null>(null)

// flowgate.v02.0049: the Minimap reads the session's active Flow only — a Graph that is not ready
// (loading, error, no tab) draws nothing, so another Flow's map never stays on screen.
const minimap = computed(() => {
  const { center } = props
  const load = center.activeGraphLoad.value
  return {
    flowId: center.state.activeFlowId,
    graph: load?.status === 'ready' ? (load.graph as FlowGraph) : null,
    viewport: center.activeView.value?.viewport ?? null,
    canvasSize: center.canvasSize.value,
  }
})

function onMiniMapViewport(viewport: CanvasViewport, commit: boolean) {
  const flowId = props.center.state.activeFlowId
  if (flowId === null) return
  if (commit) props.center.commitViewport(flowId, viewport)
  else props.center.recordViewport(flowId, viewport)
}
</script>

<style scoped>
.designer-center-column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* Takes no height of its own: it straddles the Bottom Dock's top border. */
.designer-center-column > .designer-center-resize {
  flex: none;
  margin: calc(var(--fg2-splitter-size) / -2) 0;
}
</style>
