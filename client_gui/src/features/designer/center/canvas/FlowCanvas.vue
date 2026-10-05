<template>
  <!-- flowgate.v02.0026 — read-only Canvas of one Flow (0003-D §3.3.3). The only screen part
       that uses Vue Flow: it draws the FlowGate Graph through vueFlowMapping and turns library
       events into FlowGate meaning — "select this Node / none" and "the Viewport is now ...".
       DesignerCenter keys it by Flow id, so every Flow gets its own Vue Flow instance. -->
  <div
    class="flow-canvas"
    role="region"
    :aria-label="t('gui.designer.center.canvas_label', { name: flowName })"
    data-test="designer-canvas"
    :data-flow-id="flowId"
    :data-selected-node-id="selectedNodeId ?? ''"
    :data-zoom="String(liveViewport.zoom)"
  >
    <!-- Dot grid drawn here (no extra Vue Flow package); it follows pan / zoom. -->
    <div class="flow-canvas-grid" :style="gridStyle" aria-hidden="true" />
    <VueFlow
      :id="`designer-canvas:${flowId}`"
      class="flow-canvas-flow"
      :nodes="nodes"
      :edges="edges"
      :default-viewport="initialViewport"
      :min-zoom="CANVAS_MIN_ZOOM"
      :max-zoom="CANVAS_MAX_ZOOM"
      :translate-extent="translateExtent"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :edges-updatable="false"
      :elements-selectable="false"
      :nodes-focusable="false"
      :edges-focusable="false"
      :connect-on-click="false"
      :delete-key-code="null"
      :selection-key-code="null"
      :multi-selection-key-code="null"
      :zoom-on-double-click="false"
      :disable-keyboard-a11y="true"
      :pan-on-drag="true"
      :pan-on-scroll="false"
      :zoom-on-scroll="true"
      :zoom-on-pinch="true"
      @init="onInit"
      @nodes-initialized="onNodesInitialized"
      @node-click="onNodeClick"
      @pane-click="emit('select-node', null)"
      @viewport-change="onViewportChange"
    >
      <template #node-flowgate="nodeProps">
        <FlowNodeCard :id="nodeProps.id" :data="nodeProps.data" />
      </template>
      <template #edge-flowgate="edgeProps">
        <FlowEdgePath
          :id="edgeProps.id"
          :source-x="edgeProps.sourceX"
          :source-y="edgeProps.sourceY"
          :target-x="edgeProps.targetX"
          :target-y="edgeProps.targetY"
          :source-position="edgeProps.sourcePosition"
          :target-position="edgeProps.targetPosition"
        />
      </template>
    </VueFlow>
    <p v-if="graph.nodes.length === 0" class="flow-canvas-empty" data-test="designer-canvas-empty-graph">
      {{ t('gui.designer.center.empty_graph') }}
    </p>
    <Badge v-if="sample" class="flow-canvas-sample" size="sm" tone="info" data-test="designer-canvas-sample">
      {{ t('gui.designer.center.sample_data') }}
    </Badge>
  </div>
</template>

<script setup lang="ts">
// Input: Flow id / name, FlowGraph, stored Viewport (may be none), selected Node id, sample
// flag. Output: `select-node` (Node id or null) and `viewport-change`. Never calls an API,
// changes the session, edits the Graph or handles tabs.
//
// Viewport: a stored one is restored; without one, all Nodes are fitted once they are
// measured (never above 1x) and the result is reported like any pan / zoom. A Viewport the
// session gets from elsewhere (later Minimap / Toolbar) is applied only when it differs from
// the last one this Canvas reported, so the two never feed each other.
//
// Pan bound: the view never goes further up or left than CANVAS_EDGE_MARGIN past the top-most /
// left-most Node; right and down stay open. Vue Flow applies it to pan, zoom, fit and restore.
import '@vue-flow/core/dist/style.css'
import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  VueFlow,
  type CoordinateExtent,
  type NodeMouseEvent,
  type ViewportTransform,
  type VueFlowStore,
} from '@vue-flow/core'
import Badge from '../../../../components/ui/Badge.vue'
import {
  CANVAS_FIT_MAX_ZOOM,
  CANVAS_MAX_ZOOM,
  CANVAS_MIN_ZOOM,
  DEFAULT_CANVAS_VIEWPORT,
  sameViewport,
  type CanvasViewport,
} from '../canvasView'
import type { FlowGraph } from '../graph/flowGraph'
import FlowEdgePath from './FlowEdgePath.vue'
import FlowNodeCard from './FlowNodeCard.vue'
import { toVueFlowEdges, toVueFlowNodes } from './vueFlowMapping'

const props = defineProps<{
  flowId: string
  flowName: string
  graph: FlowGraph
  viewport: CanvasViewport | null
  selectedNodeId: string | null
  sample: boolean
}>()

const emit = defineEmits<{
  'select-node': [nodeId: string | null]
  'viewport-change': [viewport: CanvasViewport]
}>()

const { t } = useI18n()

const GRID_GAP = 22
const CANVAS_EDGE_MARGIN = 40

const nodes = computed(() => toVueFlowNodes(props.graph, props.selectedNodeId))
const edges = computed(() => toVueFlowEdges(props.graph))

const translateExtent = computed<CoordinateExtent>(() => {
  const { nodes: graphNodes } = props.graph
  const left = graphNodes.length ? Math.min(...graphNodes.map((node) => node.x)) : 0
  const top = graphNodes.length ? Math.min(...graphNodes.map((node) => node.y)) : 0
  return [
    [left - CANVAS_EDGE_MARGIN, top - CANVAS_EDGE_MARGIN],
    [Infinity, Infinity],
  ]
})

const initialViewport: CanvasViewport = props.viewport ?? DEFAULT_CANVAS_VIEWPORT
/** The Viewport on screen = the last one reported to the session. */
const liveViewport = ref<CanvasViewport>(initialViewport)
const flow = shallowRef<VueFlowStore | null>(null)
let fitted = props.viewport !== null
let nodesMeasured = false

const gridStyle = computed(() => {
  const { x, y, zoom } = liveViewport.value
  const gap = GRID_GAP * zoom
  return { backgroundSize: `${gap}px ${gap}px`, backgroundPosition: `${x}px ${y}px` }
})

// Vue Flow may report the measured Nodes before or after the instance is ready; the fit runs
// once both have happened, whichever comes last.
function onInit(instance: VueFlowStore) {
  flow.value = instance
  fitOnFirstDisplay()
}

function onNodesInitialized() {
  nodesMeasured = true
  fitOnFirstDisplay()
}

function fitOnFirstDisplay() {
  if (fitted || !nodesMeasured || !flow.value || props.graph.nodes.length === 0) return
  fitted = true
  void flow.value.fitView({ padding: 0.12, minZoom: CANVAS_MIN_ZOOM, maxZoom: CANVAS_FIT_MAX_ZOOM })
}

function onNodeClick({ node }: NodeMouseEvent) {
  emit('select-node', node.id)
}

function onViewportChange({ x, y, zoom }: ViewportTransform) {
  const next = { x, y, zoom }
  if (sameViewport(liveViewport.value, next)) return
  fitted = true
  liveViewport.value = next
  emit('viewport-change', next)
}

watch(
  () => props.viewport,
  (next) => {
    if (!next || !flow.value || sameViewport(next, liveViewport.value)) return
    liveViewport.value = next
    void flow.value.setViewport({ x: next.x, y: next.y, zoom: next.zoom })
  },
)
</script>

<style scoped>
/* Fills the Canvas area; Vue Flow needs a parent with a real width and height. */
.flow-canvas {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: var(--fg2-bg);
}

.flow-canvas-grid {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(circle, rgba(154, 181, 210, 0.22) 1px, transparent 1.2px);
  pointer-events: none;
}

.flow-canvas-flow {
  position: absolute;
  inset: 0;
}

/* Vue Flow's own look is not used (its default theme stylesheet is never loaded):
   FlowNodeCard / FlowEdgePath draw everything, the library wrappers stay transparent. */
.flow-canvas-flow :deep(.vue-flow__node) {
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.flow-canvas-flow :deep(.vue-flow__pane) {
  cursor: grab;
}

.flow-canvas-flow :deep(.vue-flow__pane.dragging) {
  cursor: grabbing;
}

.flow-canvas-empty {
  position: absolute;
  top: 50%;
  left: 50%;
  margin: 0;
  color: var(--fg2-muted);
  font-size: 13px;
  transform: translate(-50%, -50%);
  pointer-events: none;
}

.flow-canvas-sample {
  position: absolute;
  top: 10px;
  right: 12px;
  pointer-events: none;
}
</style>
