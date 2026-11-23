<template>
  <!-- Orthogonal lines share side-centre ports and run below the cards. -->
  <g
    class="flow-edge"
    :class="{
      'flow-edge-editable': editable,
      'flow-edge-issue': issue,
      'flow-edge-progressed': showProgressed && !showRunning,
      'flow-edge-running': showRunning,
      'flow-edge-pending': !showProgressed && !showRunning,
      'is-selected': selected,
    }"
    data-test="designer-canvas-edge"
    :data-edge-id="id"
    data-route="ready"
    :data-edge-class="edgeClass ?? ''"
    :data-progress="showRunning ? 'running' : showProgressed ? 'progressed' : 'pending'"
    :data-protected="protectedTarget ? 'true' : 'false'"
    :data-selected="selected ? 'true' : 'false'"
    :data-deletable="deletable ? 'true' : 'false'"
    :data-source-side="anchors.sourcePosition"
    :data-target-side="anchors.targetPosition"
  >
    <title v-if="issue && issueText">{{ issueText }}</title>
    <path class="flow-edge-hit" :d="path" data-test="designer-canvas-edge-hit" />
    <path class="flow-edge-path" :d="path" data-test="designer-canvas-edge-path" />
    <path v-if="routedEdge?.drawArrow !== false" class="flow-edge-arrow" :d="arrow" data-test="designer-canvas-edge-arrow" />
    <g
      v-if="path && selected && deletable"
      class="flow-edge-remove"
      role="button"
      :aria-label="t('gui.designer.center.edge.remove')"
      :transform="`translate(${middle.x},${middle.y})`"
      data-test="designer-canvas-edge-remove"
      @click.stop="emit('remove')"
      @dblclick.stop
    >
      <title>{{ t('gui.designer.center.edge.remove') }}</title>
      <circle r="9" />
      <path d="M-3.5,-3.5 L3.5,3.5 M3.5,-3.5 L-3.5,3.5" />
    </g>
    <g
      v-else-if="path && selected && (progressed || protectedTarget)"
      class="flow-edge-locked"
      :transform="`translate(${middle.x},${middle.y})`"
      data-test="designer-canvas-edge-locked"
    >
      <title>{{ t('gui.designer.center.edge.locked') }}</title>
      <circle r="9" />
      <path d="M-3.5,-0.5 h7 v5 h-7 z M-2,-0.5 v-2 a2,2 0 0 1 4,0 v2" />
    </g>
  </g>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Position, type GraphNode } from '@vue-flow/core'
import { routeEdge, routeDrawing, type RoutedEdge } from './edgeRouting'
import { nodeRect, type EdgeAnchors } from './edgeGeometry'

const props = withDefaults(
  defineProps<{
    id: string
    sourceX: number
    sourceY: number
    targetX: number
    targetY: number
    sourcePosition?: Position
    targetPosition?: Position
    /** The two Nodes as Vue Flow holds them (live position / size); the sides come from them. */
    sourceNode?: GraphNode | null
    targetNode?: GraphNode | null
    edgeClass?: string | null
    editable?: boolean
    issue?: boolean
    /** What the last validation reported on this Edge (hover text). */
    issueText?: string
    routedEdge?: RoutedEdge
    /** Target state controls colour; progressed retains its deletion/lock meaning. */
    targetState?: string | null
    progressed?: boolean
    selected?: boolean
    deletable?: boolean
    /** a line into a done / open / review node (never removed). */
    protectedTarget?: boolean
    /** a line into a node the work is going through now. */
    running?: boolean
  }>(),
  {
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    sourceNode: null,
    targetNode: null,
    edgeClass: null,
    editable: false,
    issue: false,
    issueText: '',
    progressed: false,
    selected: false,
    deletable: false,
    protectedTarget: false,
    running: false,
  },
)

const emit = defineEmits<{
  /** The remove button of a selected, deletable Edge was clicked. */
  remove: []
}>()

const { t } = useI18n()
const showProgressed = computed(() => props.targetState != null ? props.targetState === 'done' : props.progressed)
const showRunning = computed(() => props.running || ['in_progress', 'review'].includes(props.targetState ?? ''))

const ARROW_LENGTH = 9
const ARROW_HALF_WIDTH = 5

// Unit vector the arrow points along: into the target Node through its anchor side.
const ARROW_DIRECTION: Record<Position, [number, number]> = {
  [Position.Left]: [1, 0],
  [Position.Right]: [-1, 0],
  [Position.Top]: [0, 1],
  [Position.Bottom]: [0, -1],
}

const route = computed(() => props.routedEdge ?? (
  props.sourceNode && props.targetNode
    ? routeEdge(nodeRect(props.sourceNode), nodeRect(props.targetNode))
    : null
))
const anchors = computed<EdgeAnchors>(() => route.value?.anchors ?? {
  sourceX: props.sourceX, sourceY: props.sourceY, sourcePosition: props.sourcePosition,
  targetX: props.targetX, targetY: props.targetY, targetPosition: props.targetPosition,
})

// The curve stops at the arrow's base so the line never pokes through the arrow tip.
const lineEnd = computed(() => {
  const [dx, dy] = ARROW_DIRECTION[anchors.value.targetPosition]
  return { x: anchors.value.targetX - dx * ARROW_LENGTH, y: anchors.value.targetY - dy * ARROW_LENGTH }
})

const routed = computed(() => {
  if (route.value) return routeDrawing(route.value, ARROW_LENGTH)
  const a = anchors.value
  const horizontal = a.targetPosition === Position.Left || a.targetPosition === Position.Right
  const trunk = horizontal ? (a.sourceX + a.targetX) / 2 : (a.sourceY + a.targetY) / 2
  return routeDrawing({ anchors: a, points: [
    { x: a.sourceX, y: a.sourceY },
    horizontal ? { x: trunk, y: a.sourceY } : { x: a.sourceX, y: trunk },
    horizontal ? { x: trunk, y: a.targetY } : { x: a.targetX, y: trunk },
    { x: a.targetX, y: a.targetY },
  ] }, ARROW_LENGTH)
})
const path = computed(() => routed.value.path)
const middle = computed(() => routed.value.middle)

const arrow = computed(() => {
  const { targetX, targetY, targetPosition } = anchors.value
  const [dx, dy] = ARROW_DIRECTION[targetPosition]
  const { x, y } = lineEnd.value
  const [px, py] = [-dy * ARROW_HALF_WIDTH, dx * ARROW_HALF_WIDTH]
  return `M${targetX},${targetY} L${x + px},${y + py} L${x - px},${y - py} Z`
})
</script>

<style scoped>
/* Keep the line and arrow opaque and scaled together by the Canvas viewport. */
.flow-edge-path {
  fill: none;
  stroke: var(--fg2-muted);
  stroke-width: 2;
}

.flow-edge-arrow {
  fill: var(--fg2-muted);
  stroke: none;
}

/* not yet taken — the neutral line above; already taken — bright, thicker. */
.flow-edge-progressed .flow-edge-path {
  stroke: var(--fg2-status-success-fg);
  stroke-width: 2.2;
}

.flow-edge-progressed .flow-edge-arrow {
  fill: var(--fg2-status-success-fg);
}

/* running — the info colour, dashed, moving toward the target. */
.flow-edge-running .flow-edge-path {
  stroke: var(--fg2-status-info-fg);
  stroke-width: 2.2;
  stroke-dasharray: 7 5;
  animation: flow-edge-run 0.9s linear infinite;
}

.flow-edge-running .flow-edge-arrow,
.flow-edge-running:hover .flow-edge-arrow {
  fill: var(--fg2-status-info-fg);
}

.flow-edge-running:hover .flow-edge-path {
  stroke: var(--fg2-status-info-fg);
}

@keyframes flow-edge-run {
  to {
    stroke-dashoffset: -12;
  }
}

@media (prefers-reduced-motion: reduce) {
  .flow-edge-running .flow-edge-path {
    animation: none;
  }
}

.flow-edge-hit {
  fill: none;
  stroke: transparent;
  stroke-width: 14;
  cursor: pointer;
}

.flow-edge-issue .flow-edge-path {
  stroke: #ff9d51;
}

.flow-edge-issue .flow-edge-arrow {
  fill: #ff9d51;
}

.flow-edge:hover .flow-edge-path {
  stroke: var(--fg2-control-fg);
}

.flow-edge:hover .flow-edge-arrow {
  fill: var(--fg2-control-fg);
}

.flow-edge-progressed:hover .flow-edge-path {
  stroke: var(--fg2-status-success-fg);
}

.flow-edge-progressed:hover .flow-edge-arrow {
  fill: var(--fg2-status-success-fg);
}

/* The selected Edge: a focus-coloured halo under its line, whatever its progress. */
.flow-edge.is-selected .flow-edge-hit {
  stroke: color-mix(in srgb, var(--fg2-focus) 28%, transparent);
  stroke-width: 8;
}

.flow-edge.is-selected:not(.flow-edge-progressed):not(.flow-edge-running):not(.flow-edge-issue) .flow-edge-path {
  stroke: var(--fg2-focus);
}

.flow-edge.is-selected:not(.flow-edge-progressed):not(.flow-edge-running):not(.flow-edge-issue) .flow-edge-arrow {
  fill: var(--fg2-focus);
}

.flow-edge-remove,
.flow-edge-locked {
  pointer-events: all;
}

.flow-edge-remove {
  cursor: pointer;
}

.flow-edge-remove circle {
  fill: #2a1520;
  stroke: var(--fg2-status-danger-fg);
  stroke-width: 1.4;
}

.flow-edge-remove path {
  fill: none;
  stroke: var(--fg2-status-danger-fg);
  stroke-width: 1.8;
  stroke-linecap: round;
}

.flow-edge-remove:hover circle {
  fill: #3a1a28;
}

.flow-edge-locked {
  cursor: not-allowed;
}

.flow-edge-locked circle {
  fill: #0d1a2c;
  stroke: var(--fg2-line-strong);
  stroke-width: 1.4;
}

.flow-edge-locked path {
  fill: none;
  stroke: var(--fg2-muted);
  stroke-width: 1.3;
  stroke-linejoin: round;
}
</style>
