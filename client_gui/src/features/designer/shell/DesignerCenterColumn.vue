<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. The
       Designer Center session passes through to the Workspace; the Bottom Inspector
       (flowgate.v02.0036) gets the session's selected Node; flowgate.v02.0049: the Minimap gets the
       active Flow's ready Graph, Viewport and Canvas size, and its moves go back to the session.
       flowgate.v02.0039 T#3: `unselected` (no work target yet) passes to both.
       flowgate.v02.0046: a resize handle on the Workspace ↔ Bottom Dock line asks the Shell for
       a new Bottom Dock height; the Workspace (and its Canvas) takes the rest.
       flowgate.v02.0048 T#1 (D#1 §4.2 / §5.7): the Inspector gets the session's selection context.
       Its Host intents are carried out here — select another Node, read the Flow again quietly —
       or, for a Tool Panel, passed up to the Shell.
       flowgate.v02.0070 T#1 (B1): the Inspector header's card title save goes to the session's saveNodeTitle for the
       Flow and card the edit was opened on (never the active Flow at answer time). -->
  <div ref="columnRef" class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :center="center" :unselected="unselected" :placing="placing" :move-step="moveStep" @canvas-place="(flowId, x, y) => emit('canvas-place', flowId, x, y)" @cancel-placement="emit('cancel-placement')" @show-tool="emit('show-tool', $event)" />
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
import { provide } from 'vue'
import DesignerWorkspace from './DesignerWorkspace.vue'

const props = withDefaults(
  defineProps<{
    center: DesignerCenterSession
    bottomHeight: number
    placing?: boolean
    unselected?: boolean
    /** flowgate.v02.0059 0017-T: the density's card drag step (the Shell's), for the Canvas; null = 자율형. */
    moveStep?: number | null
  }>(),
  { unselected: false, placing: false, moveStep: null },
)

// Each drag step carries the column's own height, which the Shell's clamp keeps the Workspace's room in.
const emit = defineEmits<{
  'resize-bottom': [height: number, columnHeight: number]
  'canvas-place': [flowId: string, x: number, y: number]
  'cancel-placement': []
  'show-tool': [tool: 'git']
}>()

// flowgate.v02.0048 T#1: the Bottom Dock's Inspector reads the session's selection context, and its Host
// intents (inspector/inspectorHost.ts) are carried out here, about the active Flow — or, for a
// refresh-flow naming its Flow (T#10), that open Flow even when another tab is active. Provided
// (the Dock injects the same key) so the Dock's props and events stay as they are.
provide(Symbol.for('flowgate.designer.bottomDockInspector'), {
  selection: () => props.center.activeSelection.value,
  saveTitle: props.center.canEditNodeTitles
    ? (flowId: string, nodeId: string, title: string) => props.center.saveNodeTitle(flowId, nodeId, title)
    : null,
  host(intent: { readonly kind: string; readonly nodeId?: string; readonly tool?: string; readonly flowId?: string }) {
    const flowId = props.center.state.activeFlowId
    if (intent.kind === 'select-node' && flowId !== null && intent.nodeId) props.center.selectNode(flowId, intent.nodeId)
    else if (intent.kind === 'refresh-flow') {
      const readFlowId = intent.flowId ?? flowId
      if (readFlowId !== null) props.center.refreshFlow(readFlowId)
    } else if (intent.kind === 'show-tool' && intent.tool === 'git') emit('show-tool', intent.tool)
  },
})

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
    // flowgate.v02.0068 T#2: the lines join the sides the Canvas joins, at the cards' measured sizes.
    nodeSizes: center.activeNodeSizes.value,
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
