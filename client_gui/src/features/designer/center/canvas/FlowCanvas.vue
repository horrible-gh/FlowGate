<template>
  <!-- flowgate.v02.0026 — Canvas of one Flow (0003-D §3.3.3). The only screen part that uses
       Vue Flow: it draws the FlowGate Graph through vueFlowMapping and turns library events into
       FlowGate meaning — "select this Node / none", "the Viewport is now ..." and (flowgate.v02.0045)
       "this Node was moved to ..." / "the user finished a pan / zoom". flowgate.v02.0045 S3: on an
       editable draft (`editable`) also "something was dropped here", "connect these two nodes",
       "remove this node / edge" and "undo" — the Canvas never changes the Graph itself.
       DesignerCenter keys it by Flow id, so every Flow gets its own Vue Flow instance. -->
  <div
    class="flow-canvas"
    role="region"
    :aria-label="t('gui.designer.center.canvas_label', { name: flowName })"
    data-test="designer-canvas"
    :data-flow-id="flowId"
    :data-selected-node-id="selectedNodeId ?? ''"
    :data-zoom="String(liveViewport.zoom)"
    :data-editable="editable ? 'true' : 'false'"
    tabindex="-1"
    @dragover="onDragOver"
    @drop="onDrop"
    @keydown="onKeydown"
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
      :nodes-draggable="true"
      :nodes-connectable="editable"
      :is-valid-connection="isValidConnection"
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
      @viewport-change-end="onViewportChangeEnd"
      @node-drag-stop="onNodeDragStop"
      @connect="onConnect"
      @edge-double-click="onEdgeDoubleClick"
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
          :edge-class="edgeProps.data?.edgeClass ?? null"
          :editable="edgeProps.data?.editable === true"
          :issue="edgeProps.data?.issue === true"
        />
      </template>
    </VueFlow>
    <p v-if="graph.nodes.length === 0" class="flow-canvas-empty" data-test="designer-canvas-empty-graph">
      {{ t('gui.designer.center.empty_graph') }}
    </p>
    <Badge v-if="sample" class="flow-canvas-sample" size="sm" tone="info" data-test="designer-canvas-sample">
      {{ t('gui.designer.center.sample_data') }}
    </Badge>
    <Badge
      v-else-if="ownershipLabel"
      class="flow-canvas-sample"
      size="sm"
      :tone="graph.ownership === 'conflict' ? 'danger' : 'neutral'"
      data-test="designer-canvas-ownership"
      :data-ownership="graph.ownership"
    >
      {{ ownershipLabel }}
    </Badge>
  </div>
</template>

<script setup lang="ts">
// Input: Flow id / name, FlowGraph, stored Viewport (may be none), selected Node id, sample
// flag. Output: `select-node` (Node id or null), `viewport-change`, and (flowgate.v02.0045)
// `viewport-commit` (a user pan / zoom ended — Vue Flow reports no end for a programmatic fit)
// and `node-move` (a movable Node was dropped). Never calls an API, changes the session, edits
// the Graph or handles tabs.
//
// flowgate.v02.0045 S3 (editable draft only): `canvas-drop` (the drag data and the drop point in
// Graph coordinates — what was dropped is for the session to tell), `node-connect` (a connection
// dragged between two draft nodes the `canConnect` pre-check accepts), `node-remove` (Delete /
// Backspace on the selected draft node), `edge-remove` (double click on a draft edge) and `undo`
// (Ctrl / Cmd + Z). `issueIds`: nodes / edges the last validation reported.
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
  type Connection,
  type CoordinateExtent,
  type EdgeMouseEvent,
  type NodeDragEvent,
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

const props = withDefaults(
  defineProps<{
    flowId: string
    flowName: string
    graph: FlowGraph
    viewport: CanvasViewport | null
    selectedNodeId: string | null
    sample: boolean
    /** flowgate.v02.0045 S3: the shown Graph is an editable draft. */
    editable?: boolean
    /** Connection pre-check (edit/connectionRules.ts through the session). */
    canConnect?: ((source: string, target: string) => boolean) | null
    issueIds?: readonly string[]
  }>(),
  { editable: false, canConnect: null, issueIds: () => [] },
)

const emit = defineEmits<{
  'select-node': [nodeId: string | null]
  'viewport-change': [viewport: CanvasViewport]
  'viewport-commit': [viewport: CanvasViewport]
  'node-move': [move: { nodeId: string; x: number; y: number }]
  'canvas-drop': [drop: { transfer: DataTransfer; x: number; y: number }]
  'node-connect': [connection: { source: string; target: string }]
  'node-remove': [nodeId: string]
  'edge-remove': [edgeId: string]
  undo: []
}>()

const { t } = useI18n()

const GRID_GAP = 22
const CANVAS_EDGE_MARGIN = 40

const issueSet = computed(() => new Set(props.issueIds))
const nodes = computed(() => toVueFlowNodes(props.graph, props.selectedNodeId, issueSet.value))

// Known ownership keys only (open key, P0008 §1.5): an unknown value shows no badge.
const OWNERSHIP_KEYS = ['unassigned', 'v01', 'graph', 'conflict']
const ownershipLabel = computed(() => {
  const key = props.graph.ownership
  return key && OWNERSHIP_KEYS.includes(key) ? t(`gui.designer.center.ownership.${key}`) : ''
})
const edges = computed(() => toVueFlowEdges(props.graph, issueSet.value))

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

function onViewportChangeEnd({ x, y, zoom }: ViewportTransform) {
  onViewportChange({ x, y, zoom })
  emit('viewport-commit', { x, y, zoom })
}

function onNodeDragStop({ nodes: dragged }: NodeDragEvent) {
  for (const node of dragged) {
    emit('node-move', { nodeId: node.id, x: node.position.x, y: node.position.y })
  }
}

// ── flowgate.v02.0045 S3: structure edits of an editable draft ─────────────────────────────

function onDragOver(event: DragEvent) {
  if (!props.editable || !event.dataTransfer) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
}

function onDrop(event: DragEvent) {
  if (!props.editable || !event.dataTransfer || !flow.value) return
  event.preventDefault()
  const point = flow.value.screenToFlowCoordinate({ x: event.clientX, y: event.clientY })
  emit('canvas-drop', { transfer: event.dataTransfer, x: Math.round(point.x), y: Math.round(point.y) })
}

function isValidConnection(connection: Connection): boolean {
  return props.editable && (props.canConnect?.(connection.source, connection.target) ?? false)
}

function onConnect(connection: Connection) {
  if (isValidConnection(connection)) emit('node-connect', { source: connection.source, target: connection.target })
}

function onEdgeDoubleClick({ edge }: EdgeMouseEvent) {
  if (props.editable && edge.data?.editable === true) emit('edge-remove', edge.id)
}

function onKeydown(event: KeyboardEvent) {
  if (!props.editable) return
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    emit('undo')
    return
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && props.selectedNodeId) {
    const node = props.graph.nodes.find((n) => n.id === props.selectedNodeId)
    if (node?.structureEditable && node.definition) {
      event.preventDefault()
      emit('node-remove', node.id)
    }
  }
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
