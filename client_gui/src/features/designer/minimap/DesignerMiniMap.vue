<template>
  <!-- flowgate.v02.0049 — MiniMap of the active Flow (bottom Dock, right of the Inspector). It
       draws the Graph and the Canvas's shown area that the Center session holds — never a Graph
       or Viewport of its own — and turns a press / drag on it into "show this area": the Viewport
       goes back through the session, which the Canvas applies. Same zoom, no buttons or text.
       No Graph (no tab, loading, error) or a Graph without Nodes draws nothing. -->
  <div
    class="designer-minimap"
    :class="{ 'is-dragging': dragging }"
    :aria-label="t('gui.designer.top.panels.minimap')"
    role="group"
    data-test="designer-minimap"
    :data-flow-id="flowId ?? ''"
    :data-empty="shownBounds ? 'false' : 'true'"
  >
    <svg
      v-if="shownBounds"
      ref="svgEl"
      class="designer-minimap-svg"
      :viewBox="`${shownBounds.x} ${shownBounds.y} ${shownBounds.width} ${shownBounds.height}`"
      preserveAspectRatio="xMinYMin meet"
      aria-hidden="true"
      data-test="designer-minimap-svg"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="endDrag(true)"
      @pointercancel="endDrag(true)"
      @lostpointercapture="endDrag(true)"
    >
      <line
        v-for="line in edgeLines"
        :key="line.id"
        class="designer-minimap-edge"
        :x1="line.x1"
        :y1="line.y1"
        :x2="line.x2"
        :y2="line.y2"
        vector-effect="non-scaling-stroke"
        data-test="designer-minimap-edge"
        :data-edge-id="line.id"
      />
      <rect
        v-for="node in nodeShapes"
        :key="node.id"
        class="designer-minimap-node"
        :class="{ 'is-selected': node.id === selectedNodeId }"
        :x="node.x"
        :y="node.y"
        :width="node.width"
        :height="node.height"
        rx="10"
        :fill="flowNodeKindDisplay(node.kind, node.docType).accent"
        vector-effect="non-scaling-stroke"
        data-test="designer-minimap-node"
        :data-node-id="node.id"
      />
      <template v-if="view">
        <path class="designer-minimap-mask" fill-rule="evenodd" :d="maskPath(shownBounds, view)" />
        <rect
          class="designer-minimap-viewport"
          :x="view.x"
          :y="view.y"
          :width="view.width"
          :height="view.height"
          vector-effect="non-scaling-stroke"
          data-test="designer-minimap-viewport"
        />
      </template>
    </svg>
  </div>
</template>

<script setup lang="ts">
// Input: the active Flow id, its ready Graph (null otherwise), the Canvas Viewport (null = not
// placed yet: the default one), the Canvas size (null = unknown: no shown area, no dragging) and
// the selected Node. Output: `viewport-change` while the shown area is dragged / pressed to a new
// place and `viewport-commit` once the press ends (stored like any user pan of the Canvas).
//
// The drawn area holds all Nodes and the shown area. While a drag runs, that area stays as it
// was at the press, so the map does not slide under the pointer.
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { DEFAULT_CANVAS_VIEWPORT, sameViewport, type CanvasSize, type CanvasViewport } from '../center/canvasView'
import { flowNodeKindDisplay } from '../center/canvas/flowNodeKinds'
import { toVueFlowEdges } from '../center/canvas/vueFlowMapping'
import type { FlowGraph } from '../center/graph/flowGraph'
import {
  boxToWorld,
  clampViewToMap,
  containsPoint,
  miniMapBounds,
  miniMapPanelBounds,
  miniMapEdges,
  miniMapFit,
  miniMapNodes,
  viewportAt,
  viewportRect,
  type MiniMapFit,
  type MiniMapNodeSizes,
  type WorldRect,
} from './minimapGeometry'

const props = withDefaults(
  defineProps<{
    flowId: string | null
    graph: FlowGraph | null
    viewport: CanvasViewport | null
    canvasSize: CanvasSize | null
    selectedNodeId?: string | null
    /** flowgate.v02.0068 T#2: the cards' measured sizes (the Canvas's), by node id; null = the card size. */
    nodeSizes?: MiniMapNodeSizes | null
  }>(),
  { selectedNodeId: null, nodeSizes: null },
)

const emit = defineEmits<{
  'viewport-change': [viewport: CanvasViewport]
  'viewport-commit': [viewport: CanvasViewport]
}>()

const { t } = useI18n()

interface Drag {
  pointerId: number
  bounds: WorldRect
  fit: MiniMapFit
  left: number
  top: number
  grabX: number
  grabY: number
  zoom: number
  last: CanvasViewport | null
}

const svgEl = ref<SVGSVGElement | null>(null)
const drag = shallowRef<Drag | null>(null)
const dragging = computed(() => drag.value !== null)
const panelSize = ref<CanvasSize | null>(null)
const navigationBounds = shallowRef<WorldRect | null>(null)
const requestedViewport = shallowRef<CanvasViewport | null>(null)
let resizeObserver: ResizeObserver | null = null

function measurePanel() {
  const box = svgEl.value?.getBoundingClientRect()
  if (box && box.width > 0 && box.height > 0) panelSize.value = { width: box.width, height: box.height }
}

watch(svgEl, (el) => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (!el) return
  measurePanel()
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(measurePanel)
    resizeObserver.observe(el)
  }
}, { flush: 'post', immediate: true })
onBeforeUnmount(() => resizeObserver?.disconnect())

const nodeShapes = computed(() => (props.graph ? miniMapNodes(props.graph, props.nodeSizes) : []))
// The same execution Edges the Canvas draws (reference / dangling / self edges are left out there).
const edgeLines = computed(() => {
  if (!props.graph) return []
  return miniMapEdges(props.graph, new Set(toVueFlowEdges(props.graph).map((edge) => edge.id)), props.nodeSizes)
})
const currentViewport = computed(() => props.viewport ?? DEFAULT_CANVAS_VIEWPORT)
const view = computed(() => (nodeShapes.value.length ? viewportRect(currentViewport.value, props.canvasSize) : null))
const bounds = computed(() => {
  const content = miniMapBounds(nodeShapes.value, view.value)
  return content ? miniMapPanelBounds(content, panelSize.value ?? { width: 1, height: 1 }) : null
})
const shownBounds = computed(() => (bounds.value ? (drag.value?.bounds ?? navigationBounds.value ?? bounds.value) : null))

// A map move keeps the map that existed before that move. Canvas pan, Graph edits and resize
// release that snapshot, allowing exploration to expand and contract the map.
watch(() => props.viewport, (next) => {
  if (requestedViewport.value && sameViewport(next, requestedViewport.value)) return
  navigationBounds.value = null
  requestedViewport.value = null
})
watch([nodeShapes, () => props.canvasSize, panelSize], () => { navigationBounds.value = null })

function maskPath(outer: WorldRect, inner: WorldRect): string {
  const rect = (r: WorldRect) => `M${r.x} ${r.y}h${r.width}v${r.height}h${-r.width}z`
  return `${rect(outer)}${rect(inner)}`
}

function onPointerDown(event: PointerEvent) {
  const area = view.value
  const el = svgEl.value
  if (event.button !== 0 || drag.value || !area || !el) return
  measurePanel()
  const drawn = shownBounds.value
  if (!drawn) return
  const box = el.getBoundingClientRect()
  const fit = miniMapFit(drawn, { width: box.width, height: box.height })
  if (!fit) return
  event.preventDefault()
  const point = boxToWorld({ x: event.clientX - box.left, y: event.clientY - box.top }, drawn, fit)
  // A press on the shown area grabs it where it was pressed; elsewhere it centers the area there.
  const inside = containsPoint(area, point)
  navigationBounds.value = drawn
  drag.value = {
    pointerId: event.pointerId,
    bounds: drawn,
    fit,
    left: box.left,
    top: box.top,
    grabX: inside ? point.x - area.x : area.width / 2,
    grabY: inside ? point.y - area.y : area.height / 2,
    zoom: currentViewport.value.zoom,
    last: null,
  }
  el.setPointerCapture?.(event.pointerId)
  if (!inside) moveTo(event)
}

function onPointerMove(event: PointerEvent) {
  if (drag.value && event.pointerId === drag.value.pointerId) moveTo(event)
}

function moveTo(event: PointerEvent) {
  const current = drag.value
  if (!current) return
  const point = boxToWorld({ x: event.clientX - current.left, y: event.clientY - current.top }, current.bounds, current.fit)
  const area = view.value
  if (!area) return
  const position = clampViewToMap(point.x - current.grabX, point.y - current.grabY, area, current.bounds)
  const next = viewportAt(position.x, position.y, current.zoom)
  if (!Number.isFinite(next.x) || !Number.isFinite(next.y) || sameViewport(current.last, next)) return
  current.last = next
  requestedViewport.value = next
  emit('viewport-change', next)
}

function endDrag(commit: boolean) {
  const current = drag.value
  if (!current) return
  drag.value = null
  if (svgEl.value?.hasPointerCapture?.(current.pointerId)) svgEl.value.releasePointerCapture(current.pointerId)
  if (commit && current.last) emit('viewport-commit', current.last)
}

// Another Flow (or none) — a drag of the previous one never reaches it.
watch(
  () => [props.flowId, props.graph === null] as const,
  () => {
    endDrag(false)
    navigationBounds.value = null
    requestedViewport.value = null
  },
)
</script>

<style scoped>
.designer-minimap {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}

.designer-minimap-svg {
  display: block;
  width: 100%;
  height: 100%;
  cursor: pointer;
  touch-action: none;
  user-select: none;
}

.designer-minimap.is-dragging .designer-minimap-svg {
  cursor: grabbing;
}

.designer-minimap-edge {
  stroke: var(--fg2-muted-2);
  stroke-width: 1.25;
}

.designer-minimap-node {
  fill-opacity: 0.55;
  stroke: rgba(238, 245, 255, 0.18);
  stroke-width: 1;
}

.designer-minimap-node.is-selected {
  fill-opacity: 0.9;
  stroke: var(--fg2-text);
}

.designer-minimap-mask {
  fill: rgba(7, 17, 29, 0.55);
  pointer-events: none;
}

.designer-minimap-viewport {
  fill: rgba(75, 163, 255, 0.06);
  stroke: var(--fg2-blue);
  stroke-width: 1.5;
}
</style>
