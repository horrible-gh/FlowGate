<template>
  <!-- flowgate.v02.0026 — Canvas of one Flow (0003-D §3.3.3). The only screen part that uses
       Vue Flow: it draws the FlowGate Graph through vueFlowMapping and turns library events into
       FlowGate meaning — "select this Node / none", "the Viewport is now ..." and (flowgate.v02.0045)
       "this Node was moved to ..." / "the user finished a pan / zoom". flowgate.v02.0045 S3: on an
       editable draft (`editable`) also "something was dropped here", "connect these two nodes",
       "remove this node / edge" and "undo" — the Canvas never changes the Graph itself.
       DesignerCenter keys it by Flow id, so every Flow gets its own Vue Flow instance.
       flowgate.v02.0045 T0019: no empty-Graph text and no ownership badge; an Edge can be selected
       (click) and, when deletable, removed (its remove button or Delete / Backspace).
       flowgate.v02.0045 TR0020 rev2: while a connection is dragged (`is-connecting`) every
       connectable card is a drop area as a whole (FlowNodeCard). TR0020 rev4: nothing is laid
       over the Canvas (no edit note); an Edge gets its two Nodes so it attaches to the facing
       sides (FlowEdgePath); the last validation's problems only mark their cards / lines.
       flowgate.v02.0059 T#1 (0011-CH contract A / B): in the Toolbar's [선택] mode (`select-mode`) a
       drag on the empty Canvas selects the cards in its rectangle, a card click adds / removes it,
       the wheel (and the MiniMap) pans, and dragging a selected card moves the selection together.
       Out of the mode every Vue Flow setting is the one it had before.
       flowgate.v02.0059 0017-T: a dragged card (or the dragged selection) moves in whole steps of
       `move-step` (the density's) while it is dragged and where it is dropped; none = 자율형.
       0018-TR rework: the dot grid is that step's lattice (a dot on every whole-step Graph point, following
       pan / zoom), and a dragged card lands on one of its dots; 자율형 has no lattice and shows no grid. -->
  <div
    ref="canvasEl"
    class="flow-canvas"
    :class="{ 'is-connecting': connecting, 'is-placing': placing, 'is-select-mode': selectMode }"
    role="region"
    :aria-label="t('gui.designer.center.canvas_label', { name: flowName })"
    data-test="designer-canvas"
    :data-flow-id="flowId"
    :data-selected-node-id="selectedNodeId ?? ''"
    :data-selected-node-ids="selection.join(' ')"
    :data-select-mode="selectMode ? 'true' : 'false'"
    :data-selected-edge-id="selectedEdge?.id ?? ''"
    :data-zoom="String(liveViewport.zoom)"
    :data-move-step="moveStep ?? ''"
    :data-editable="editable ? 'true' : 'false'"
    tabindex="-1"
    @dragover="onDragOver"
    @drop="onDrop"
    @keydown="onKeydown"
  >
    <!-- Dot grid drawn here (no extra Vue Flow package); it follows pan / zoom. 0018-TR rework: its dots are
         the move step's lattice points (canvasView.ts canvasDotGrid). -->
    <div
      v-if="grid"
      class="flow-canvas-grid"
      :style="gridStyle"
      aria-hidden="true"
      data-test="designer-canvas-grid"
      :data-grid-gap="String(grid.gap)"
      :data-grid-size="String(grid.size)"
      :data-grid-origin="`${grid.x} ${grid.y}`"
    />
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
      :elements-selectable="selectMode"
      :nodes-focusable="false"
      :edges-focusable="false"
      :connect-on-click="false"
      :connection-radius="CONNECTION_RADIUS"
      :delete-key-code="null"
      :selection-key-code="selectMode ? true : null"
      :multi-selection-key-code="selectMode ? true : null"
      :select-nodes-on-drag="!selectMode"
      :zoom-on-double-click="false"
      :disable-keyboard-a11y="true"
      :pan-on-drag="!selectMode"
      :pan-on-scroll="selectMode"
      :zoom-on-scroll="true"
      :zoom-on-pinch="true"
      @init="onInit"
      @nodes-initialized="onNodesInitialized"
      @nodes-change="onNodesChange"
      @node-click="onNodeClick"
      @pane-click="onPaneClick"
      @viewport-change="onViewportChange"
      @viewport-change-end="onViewportChangeEnd"
      @node-drag-start="onNodeDragStart"
      @node-drag="onNodeDrag"
      @node-drag-stop="onNodeDragStop"
      @connect="onConnect"
      @connect-start="connecting = true"
      @connect-end="connecting = false"
      @edge-click="onEdgeClick"
      @edge-double-click="onEdgeDoubleClick"
    >
      <template #node-flowgate="nodeProps">
        <FlowNodeCard :id="nodeProps.id" :data="nodeProps.data" @approve="emit('node-approve', $event)" @conflict="emit('node-conflict', $event)" />
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
          :source-node="edgeProps.sourceNode"
          :target-node="edgeProps.targetNode"
          :edge-class="edgeProps.data?.edgeClass ?? null"
          :editable="edgeProps.data?.editable === true"
          :issue="edgeProps.data?.issue === true"
          :issue-text="edgeProps.data?.issueText ?? ''"
          :progressed="edgeProps.data?.progressed === true"
          :selected="edgeProps.data?.selected === true"
          :deletable="edgeProps.data?.deletable === true"
          :protected-target="edgeProps.data?.protected === true"
          :running="edgeProps.data?.running === true"
          @remove="removeEdge(edgeProps.id)"
        />
      </template>
    </VueFlow>
    <Badge v-if="sample" class="flow-canvas-sample" size="sm" tone="info" data-test="designer-canvas-sample">
      {{ t('gui.designer.center.sample_data') }}
    </Badge>
    <!-- flowgate.v02.0061 T#14: why the structure cannot be edited now (the run lock; TR0031 rev1: or the run status
         not confirmed yet); viewing stays as it is. -->
    <Badge
      v-if="lockText"
      class="flow-canvas-lock"
      size="sm"
      tone="warning"
      role="status"
      :title="lockText"
      data-test="designer-canvas-lock"
      :data-lock="marks.lock ?? ''"
    >
      🔒 {{ lockText }}
    </Badge>
  </div>
</template>

<script setup lang="ts">
// Input: Flow id / name, FlowGraph, stored Viewport (may be none), selected Node id, sample
// flag. Output: `select-node` (Node id or null), `viewport-change`, and (flowgate.v02.0045)
// `viewport-commit` (a user pan / zoom ended — Vue Flow reports no end for a programmatic fit)
// and `nodes-move` (movable Nodes were dropped — flowgate.v02.0059 T#2: one event per drop). Never calls an API, changes the session, edits
// the Graph or handles tabs. flowgate.v02.0049: `canvas-resize` — the Canvas area size Vue Flow
// measured (it watches the resize itself), when shown and on every change, for the MiniMap.
//
// flowgate.v02.0045 S3 (editable draft only): `canvas-drop` (the drag data and the drop point in
// Graph coordinates — what was dropped is for the session to tell), `node-connect` (a connection
// dragged between two draft nodes the `canConnect` pre-check accepts), `node-remove` (Delete /
// Backspace on the selected draft node), `edge-remove` (double click on a draft edge) and `undo`
// (Ctrl / Cmd + Z). `issues`: what the last validation reported — its nodes / edges are marked
// and name the problem on hover; nothing else is shown for it (TR0020 rev4).
//
// Viewport: a stored one is restored; without one, all Nodes are fitted once they are
// measured (never above 1x) and the result is reported like any pan / zoom. A Viewport the
// session gets from elsewhere (later Minimap / Toolbar) is applied only when it differs from
// the last one this Canvas reported, so the two never feed each other.
//
// Pan bound: the view never goes further up or left than CANVAS_EDGE_MARGIN past the top-most /
// left-most Node; right and down stay open. Vue Flow applies it to pan, zoom, fit and restore.
//
// flowgate.v02.0045 T0019 — Edge selection is the Canvas's own (one Edge or none, never together
// with a selected Node): a click on an Edge selects it, a click on a Node or the pane clears it.
// Selecting is allowed for every Edge; removing (`edge-remove`) only for a deletable one — an
// editable draft Edge the work has not gone through (vueFlowMapping `deletable`).
//
// flowgate.v02.0059 T#1 — Selection: `selectedNodeIds` (or the single `selectedNodeId` alone) is
// what is drawn selected. In [선택] mode (`selectMode`) Vue Flow's own area selection and click
// toggle run; every selection change it reports is applied to the shown Selection and sent up as
// `select-nodes` (all selected Node ids) — Vue Flow never decides the Selection by itself. A drop
// reports each dragged Node's position once, the group clamped to the Canvas origin as a whole.
//
// flowgate.v02.0059 T#2 (0011-CH contract C): a drop is one `nodes-move` (every dragged Node's position) —
// one move of the Flow's undo / redo, whether one card or the selection was dragged. Ctrl / Cmd + Z (`undo`)
// works on every Flow, not only an editable draft: card moves are undone too.
//
// flowgate.v02.0059 0017-T — move step: `moveStep` (the Toolbar / View menu density's,
// top/designerTop.ts DENSITY_MOVE_STEP) is how far apart the places a drag can leave cards are. While
// cards are dragged they are shown in whole steps from where the drag started, the same offset for each
// (canvasView.ts stepGroupMove), so the Edges follow the stepped cards and a selection keeps its layout; the
// drop reports the stepped positions.
// No step (자율형) leaves the drag as Vue Flow makes it. A new step moves no card.
//
// 0018-TR rework — the step is a lattice: the card under the pointer lands on the whole-step Graph point nearest
// to the pointer (the others keep their offset to it), a drop pushed back inside the origin moves in whole steps,
// and the dot grid draws exactly those points (canvasView.ts canvasDotGrid) — a new step redraws the grid only.
import '@vue-flow/core/dist/style.css'
import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  VueFlow,
  type Connection,
  type CoordinateExtent,
  type EdgeMouseEvent,
  type GraphNode,
  type NodeChange,
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
  canvasDotGrid,
  clampCanvasZoom,
  clampGroupToCanvas,
  sameViewport,
  stepGroupMove,
  type CanvasSize,
  type CanvasViewport,
} from '../canvasView'
import type { CanvasViewRequest } from '../designerCenterSession'
import type { FlowGraph } from '../graph/flowGraph'
import type { FlowDraftIssue } from '../graph/flowGraphSource'
import FlowEdgePath from './FlowEdgePath.vue'
import FlowNodeCard from './FlowNodeCard.vue'
import { toVueFlowEdges, toVueFlowNodes } from './vueFlowMapping'
import { NO_RUN_MARKS, type FlowRunMarks } from '../run/flowRunLock'

const props = withDefaults(
  defineProps<{
    flowId: string
    flowName: string
    graph: FlowGraph
    viewport: CanvasViewport | null
    selectedNodeId: string | null
    sample: boolean
    /** flowgate.v02.0059 T#1: every selected Node (empty = `selectedNodeId` alone is shown selected). */
    selectedNodeIds?: readonly string[]
    /** flowgate.v02.0059 T#1: the Toolbar's [선택] mode — area / click multi-selection. */
    selectMode?: boolean
    /** flowgate.v02.0059 T#1: the latest fit / zoom asked of this Canvas (Toolbar, View menu). */
    viewRequest?: CanvasViewRequest | null
    /** flowgate.v02.0045 S3: the shown Graph is an editable draft. */
    editable?: boolean
    placing?: boolean
    /** Connection pre-check (edit/connectionRules.ts through the session). */
    canConnect?: ((source: string, target: string) => boolean) | null
    issues?: readonly FlowDraftIssue[]
    /** flowgate.v02.0059 0017-T: the step a card drag moves in (Graph units); null = 자율형 (none). */
    moveStep?: number | null
    /** flowgate.v02.0056 F2-1: a card waiting for its approval offers [승인] (a `node-approve` event). */
    canApprove?: boolean
    /** flowgate.v02.0056 F2-1: the card whose [승인] is being answered, or null. */
    approvingNodeId?: string | null
    /**
     * flowgate.v02.0061 T#14: the Flow's run marks (run/flowRunLock.ts) — the edit lock and its note, the protected
     * lines and the running lines. `editable` already leaves the lock out (DesignerCenter).
     */
    marks?: FlowRunMarks
  }>(),
  { selectedNodeIds: () => [], selectMode: false, viewRequest: null, editable: false, placing: false, canConnect: null, issues: () => [], moveStep: null, canApprove: false, approvingNodeId: null, marks: () => NO_RUN_MARKS },
)

const emit = defineEmits<{
  'select-node': [nodeId: string | null]
  /** flowgate.v02.0059 T#1: [선택] mode — the Selection is now these Nodes. */
  'select-nodes': [nodeIds: string[]]
  'viewport-change': [viewport: CanvasViewport]
  'viewport-commit': [viewport: CanvasViewport]
  'canvas-resize': [size: CanvasSize]
  /** flowgate.v02.0059 T#2: one drop — the position of every dropped Node (one undo entry). */
  'nodes-move': [moves: { nodeId: string; x: number; y: number }[]]
  'canvas-drop': [drop: { transfer: DataTransfer; x: number; y: number }]
  'canvas-place': [point: { x: number; y: number }]
  'cancel-placement': []
  'node-connect': [connection: { source: string; target: string }]
  'node-remove': [nodeId: string]
  'edge-remove': [edgeId: string]
  undo: []
  /** flowgate.v02.0056 F2-1: a card's [승인]. */
  'node-approve': [nodeId: string]
  /** flowgate.v02.0061 T#14: a card's 병합 충돌 (its result's merge conflict) was pressed. */
  'node-conflict': [nodeId: string]
}>()

const { t } = useI18n()

/** The first display and 화면 맞춤 fit the same way: never above 1x. */
const FIT_OPTIONS = { padding: 0.12, minZoom: CANVAS_MIN_ZOOM, maxZoom: CANVAS_FIT_MAX_ZOOM }
// The Graph origin bounds direct Canvas pan on the top/left; right and bottom stay open.
/** flowgate.v02.0045 T0019: how near a handle a dragged connection snaps to it (Vue Flow: 20). */
const CONNECTION_RADIUS = 36

const canvasEl = ref<HTMLElement | null>(null)
const selectedEdgeId = ref<string | null>(null)
/** flowgate.v02.0045 TR0020 rev2: a connection is being dragged (cards become drop areas). */
const connecting = ref(false)

// Node / Edge id -> the problems reported on it, one per line.
const issueMap = computed(() => {
  const map = new Map<string, string>()
  for (const issue of props.issues) {
    const text = issue.message || issue.code
    for (const id of [...issue.nodeIds, ...issue.edgeIds]) map.set(id, map.has(id) ? `${map.get(id)}\n${text}` : text)
  }
  return map
})
/** The selected Node ids drawn as selected. */
const selection = computed<readonly string[]>(() =>
  props.selectedNodeIds.length > 0 ? props.selectedNodeIds : props.selectedNodeId === null ? [] : [props.selectedNodeId],
)
const nodes = computed(() =>
  toVueFlowNodes(props.graph, selection.value, issueMap.value, {
    selectMode: props.selectMode,
    canApprove: props.canApprove,
    approvingNodeId: props.approvingNodeId,
    marks: props.marks,
  }),
)
const edges = computed(() => toVueFlowEdges(props.graph, issueMap.value, selectedEdgeId.value, props.marks))
/** flowgate.v02.0061 T#14: the run lock's note (null when the structure may be edited); TR0031 rev1: `unknown` too. */
const lockText = computed(() => {
  if (props.marks.lock === 'run') return t('gui.designer.center.run_lock')
  return props.marks.lock === 'unknown' ? t('gui.designer.center.run_lock_pending') : null
})
/** The selected Edge as drawn, or null (also when it left the Graph). */
const selectedEdge = computed(() => edges.value.find((edge) => edge.id === selectedEdgeId.value) ?? null)

const translateExtent = computed<CoordinateExtent>(() => {
  const { nodes: graphNodes } = props.graph
  const left = graphNodes.length ? Math.min(0, ...graphNodes.map((node) => node.x)) : 0
  const top = graphNodes.length ? Math.min(0, ...graphNodes.map((node) => node.y)) : 0
  return [
    [left, top],
    [Infinity, Infinity],
  ]
})

const initialViewport: CanvasViewport = props.viewport ?? DEFAULT_CANVAS_VIEWPORT
/** The Viewport on screen = the last one reported to the session. */
const liveViewport = ref<CanvasViewport>(initialViewport)
const flow = shallowRef<VueFlowStore | null>(null)
let fitted = props.viewport !== null
let nodesMeasured = false

/**
 * 0018-TR rework: the move step's dot grid on the Viewport Vue Flow draws the cards with (its own transform, which
 * also holds a first view it constrained without reporting it); none for 자율형.
 */
const grid = computed(() => canvasDotGrid(flow.value?.viewport.value ?? liveViewport.value, props.moveStep))
// Each dot is drawn in the middle of its background tile, so the tiles start half a tile before the Graph origin:
// the dots fall on the origin and every whole gap from it.
const gridStyle = computed(() => {
  const value = grid.value
  if (!value) return {}
  const { size, x, y } = value
  return { backgroundSize: `${size}px ${size}px`, backgroundPosition: `${x - size / 2}px ${y - size / 2}px` }
})

// Vue Flow may report the measured Nodes before or after the instance is ready; the fit runs
// once both have happened, whichever comes last.
function onInit(instance: VueFlowStore) {
  flow.value = instance
  syncMultiSelection()
  fitOnFirstDisplay()
}

// flowgate.v02.0059 T#1: in [선택] mode a card click adds / removes it. Vue Flow turns its
// multi-selection on only when the key state changes, which never happens for a Canvas shown while
// the mode is already on (another tab), so the Canvas sets it from the mode itself.
function syncMultiSelection() {
  if (flow.value) flow.value.multiSelectionActive.value = props.selectMode
}

function onNodesInitialized() {
  nodesMeasured = true
  fitOnFirstDisplay()
}

function fitOnFirstDisplay() {
  if (fitted || !nodesMeasured || !flow.value || props.graph.nodes.length === 0) return
  fitted = true
  void flow.value.fitView(FIT_OPTIONS)
}

// flowgate.v02.0059 T#1: fit / zoom asked from the Toolbar or the View menu. They run on the Vue
// Flow instance, so zoom stays inside CANVAS_MIN_ZOOM..MAX_ZOOM, steps and presets zoom about the
// Canvas centre and the pan bound (translateExtent) applies as for any other change. Vue Flow
// reports no "end" for a programmatic change, so the result is committed here (stored per Flow).
async function applyViewRequest(request: CanvasViewRequest) {
  const instance = flow.value
  if (!instance) return
  fitted = true
  if (request.kind === 'fit') {
    // Nothing to fit on an empty Graph: show its default view.
    if (props.graph.nodes.length === 0) await instance.setViewport({ ...DEFAULT_CANVAS_VIEWPORT })
    else await instance.fitView(FIT_OPTIONS)
  } else if (request.kind === 'zoom-in') {
    await instance.zoomIn()
  } else if (request.kind === 'zoom-out') {
    await instance.zoomOut()
  } else if (Number.isFinite(request.zoom)) {
    await instance.zoomTo(clampCanvasZoom(request.zoom))
  }
  const { x, y, zoom } = liveViewport.value
  emit('viewport-commit', { x, y, zoom })
}

function onNodeClick({ node }: NodeMouseEvent) {
  selectedEdgeId.value = null
  // [선택] mode: the click toggled the Node in Vue Flow's selection (onNodesChange sent it up).
  if (props.selectMode) return
  emit('select-node', node.id)
}

// flowgate.v02.0059 T#1: the Selection as last sent up — Vue Flow may report several changes before
// the session's answer comes back as props, and each builds on the one before.
let reportedSelection: readonly string[] | null = null
watch(selection, () => {
  reportedSelection = null
})

function onNodesChange(changes: NodeChange[]) {
  if (!props.selectMode) return
  const before = reportedSelection ?? selection.value
  const next = new Set(before)
  let reported = false
  for (const change of changes) {
    if (change.type !== 'select') continue
    reported = true
    if (change.selected) next.add(change.id)
    else next.delete(change.id)
  }
  if (!reported) return
  const ids = props.graph.nodes.filter((node) => next.has(node.id)).map((node) => node.id)
  if (ids.length === before.length && ids.every((id) => before.includes(id))) return
  reportedSelection = ids
  selectedEdgeId.value = null
  emit('select-nodes', ids)
}

function onPaneClick(event: MouseEvent) {
  selectedEdgeId.value = null
  if (props.placing && props.editable && flow.value) {
    const point = flow.value.screenToFlowCoordinate({ x: event.clientX, y: event.clientY })
    emit('canvas-place', { x: Math.max(0, Math.round(point.x)), y: Math.max(0, Math.round(point.y)) })
    return
  }
  emit('select-node', null)
}

// ── flowgate.v02.0045 T0019: Edge selection and removal ───────────────────────────────────────

function onEdgeClick({ edge }: EdgeMouseEvent) {
  selectedEdgeId.value = edge.id
  // The whole Node selection ends, one Node or a [선택] group (selectedNodeId is null for a group).
  if (selection.value.length > 0) emit('select-node', null)
  // Delete / Backspace reach the Canvas only while it has the focus.
  canvasEl.value?.focus({ preventScroll: true })
}

/** Removes an Edge when it may be removed; a progressed or read-only Edge stays. */
function removeEdge(edgeId: string): boolean {
  const edge = edges.value.find((candidate) => candidate.id === edgeId)
  if (!props.editable || edge?.data?.deletable !== true) return false
  if (selectedEdgeId.value === edgeId) selectedEdgeId.value = null
  emit('edge-remove', edgeId)
  return true
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

// flowgate.v02.0059 T#1 (contract B): one drop of one Node or of the dragged selection — the group
// is kept inside the Canvas origin as a whole, so the Nodes keep their relative layout. Vue Flow
// already shows each dragged card where the pointer left it; the clamped position is put back on
// the card itself, because a group clamped back onto the place it started from leaves the Graph
// unchanged (moveNode keeps it) and then no redraw from the Graph would follow.
function onNodeDragStop({ node, nodes: dragged }: NodeDragEvent) {
  const drops = clampGroupToCanvas(steppedPositions(dragged, node?.id), props.moveStep)
  dragStarts = new Map()
  for (const drop of drops) {
    const shown = flow.value?.findNode(drop.nodeId)
    if (shown && (shown.position.x !== drop.x || shown.position.y !== drop.y)) shown.position = { x: drop.x, y: drop.y }
  }
  if (drops.length) emit('nodes-move', drops)
}

// flowgate.v02.0059 0017-T: where each dragged card was shown when its drag started. A drop seen without
// its start takes the card's Graph position, which is where it was shown (the Graph changes on a drop only).
let dragStarts = new Map<string, { x: number; y: number }>()

function onNodeDragStart({ nodes: dragged }: NodeDragEvent) {
  dragStarts = new Map(dragged.map((node) => [node.id, { x: node.position.x, y: node.position.y }]))
}

/** The card under the pointer on its nearest step point, the others at its offset; without a step, where Vue Flow put them. */
function steppedPositions(dragged: readonly GraphNode[], anchorId?: string | null) {
  return stepGroupMove(
    dragged.map((node) => ({ nodeId: node.id, x: node.position.x, y: node.position.y })),
    (nodeId) => dragStarts.get(nodeId) ?? props.graph.nodes.find((node) => node.id === nodeId) ?? null,
    props.moveStep,
    anchorId,
  )
}

// Vue Flow moves every dragged card to the pointer on each move and then reports the drag; the cards are put
// on their steps right then, before anything is drawn. Each gets a new position object: the one Vue Flow set
// is its own drag state, which it keeps following the pointer from.
function onNodeDrag({ node, nodes: dragged }: NodeDragEvent) {
  if (!props.moveStep) return
  for (const place of steppedPositions(dragged, node?.id)) {
    const shown = dragged.find((candidate) => candidate.id === place.nodeId)
    if (shown && (shown.position.x !== place.x || shown.position.y !== place.y)) shown.position = { x: place.x, y: place.y }
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
  emit('canvas-drop', { transfer: event.dataTransfer, x: Math.max(0, Math.round(point.x)), y: Math.max(0, Math.round(point.y)) })
}

// flowgate.v02.0045 T0019: Vue Flow also runs this on every Edge it is handed (its setEdges), not
// only on a dragged connection — an Edge already in the Graph is drawn as it is, or a redraw of the
// Edges (a selection, a validation mark) would drop them all.
function isValidConnection(connection: Connection): boolean {
  const id = (connection as Connection & { id?: unknown }).id
  if (typeof id === 'string' && props.graph.edges.some((edge) => edge.id === id)) return true
  return props.editable && (props.canConnect?.(connection.source, connection.target) ?? false)
}

function onConnect(connection: Connection) {
  if (isValidConnection(connection)) emit('node-connect', { source: connection.source, target: connection.target })
}

function onEdgeDoubleClick({ edge }: EdgeMouseEvent) {
  removeEdge(edge.id)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.placing) {
    event.preventDefault()
    emit('cancel-placement')
    return
  }
  const edge = selectedEdge.value
  if (edge && event.key === 'Escape') {
    selectedEdgeId.value = null
    return
  }
  if (edge && (event.key === 'Delete' || event.key === 'Backspace')) {
    // A selected Edge takes the key even when it cannot be removed (nothing else is removed).
    event.preventDefault()
    removeEdge(edge.id)
    return
  }
  // flowgate.v02.0059 T#2: card moves share the undo, so it is not bound to an editable draft any more.
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    emit('undo')
    return
  }
  if (!props.editable) return
  if ((event.key === 'Delete' || event.key === 'Backspace') && props.selectedNodeId) {
    const node = props.graph.nodes.find((n) => n.id === props.selectedNodeId)
    if (node?.structureEditable && node.definition) {
      event.preventDefault()
      emit('node-remove', node.id)
    }
  }
}

// A Node selected from anywhere (Canvas, Inspector, 전체 선택, ...) ends the Edge selection — one
// Node or several (flowgate.v02.0059 T#1: Vue Flow never clears it, its Edges are not selectable).
watch(selection, (nodeIds) => {
  if (nodeIds.length > 0) selectedEdgeId.value = null
})

watch(
  () => {
    const size = flow.value?.dimensions.value
    return size ? `${size.width}x${size.height}` : ''
  },
  () => {
    const size = flow.value?.dimensions.value
    if (size && size.width > 0 && size.height > 0) emit('canvas-resize', { width: size.width, height: size.height })
  },
)

watch(() => props.selectMode, syncMultiSelection, { flush: 'post' })

watch(
  () => props.viewRequest,
  (request) => {
    if (request) void applyViewRequest(request)
  },
)

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

.flow-canvas.is-placing :deep(.vue-flow__pane) {
  cursor: crosshair;
}

/* flowgate.v02.0059 T#1: [선택] mode — a drag on the empty Canvas draws the selection rectangle. */
.flow-canvas.is-select-mode :deep(.vue-flow__pane) {
  cursor: crosshair;
}

.flow-canvas-flow :deep(.vue-flow__selection) {
  border: 1px dashed rgba(88, 166, 255, 0.85);
  background: rgba(88, 166, 255, 0.1);
}

/* Vue Flow's own box around the selected cards is not used: the cards stay clickable (add /
   remove) and a selection is dragged by any of its cards, through the usual Node drop. */
.flow-canvas-flow :deep(.vue-flow__nodesselection) {
  display: none;
}

/* flowgate.v02.0045 TR0020 rev2: while a connection is dragged, a connectable card takes the
   drop anywhere on it (its whole-card target handle) and lights up under the pointer; the start
   strips step aside so they never catch the drop. */
.flow-canvas.is-connecting :deep(.flow-node-drop) {
  pointer-events: all;
  cursor: crosshair;
}

.flow-canvas.is-connecting :deep(.flow-node-link) {
  pointer-events: none;
}

.flow-canvas.is-connecting :deep(.flow-node-drop:hover),
.flow-canvas.is-connecting :deep(.flow-node-drop.vue-flow__handle-valid) {
  background: color-mix(in srgb, var(--flow-node-accent) 14%, transparent);
  box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--flow-node-accent) 75%, transparent);
}

.flow-canvas-sample {
  position: absolute;
  top: 10px;
  right: 12px;
  pointer-events: none;
}

/* flowgate.v02.0061 T#14: the run lock's note, top left (the sample badge keeps the top right). */
.flow-canvas-lock {
  position: absolute;
  top: 10px;
  left: 12px;
  max-width: calc(100% - 24px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  pointer-events: none;
}
</style>
