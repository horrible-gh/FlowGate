<template>
  <!-- flowgate.v02.0026 — FlowGate Edge (0003-D §6.3): a smooth curve ending in an arrow head
       at the target anchor, in the FlowGate line colour. Rendered inside Vue Flow's edge
       <svg>; it draws the arrow itself instead of using the library's marker / theme. -->
  <!-- flowgate.v02.0045 S3: an editable draft edge has a wider hit line (double click removes it,
       FlowCanvas); one the last validation reported is drawn in the warning colour and names the
       problem on hover. -->
  <!-- flowgate.v02.0045 T0019: an Edge the work already went through (`progressed`) is drawn in a
       bright solid colour, one not yet taken stays a faint line. Every Edge has a wide hit line so
       it can be selected (click); the selected Edge is highlighted. Selection and removal are apart:
       only a selected Edge that is `deletable` shows the remove button — a progressed one shows
       that it cannot be removed instead. -->
  <!-- flowgate.v02.0045 TR0020 rev4: one kind of line only — a reference relation is not drawn
       (vueFlowMapping). The Edge leaves its source card and enters its target card on the sides
       that face each other (edgeGeometry.ts over the live node rectangles, `sourceNode` /
       `targetNode`), so a card moved below another one is reached from its top, not always from
       the left. Without the two nodes (a bare use) the anchor props Vue Flow computed are drawn. -->
  <g
    class="flow-edge"
    :class="{
      'flow-edge-editable': editable,
      'flow-edge-issue': issue,
      'flow-edge-progressed': progressed,
      'flow-edge-pending': !progressed,
      'is-selected': selected,
    }"
    data-test="designer-canvas-edge"
    :data-edge-id="id"
    :data-edge-class="edgeClass ?? ''"
    :data-progress="progressed ? 'progressed' : 'pending'"
    :data-selected="selected ? 'true' : 'false'"
    :data-deletable="deletable ? 'true' : 'false'"
    :data-source-side="anchors.sourcePosition"
    :data-target-side="anchors.targetPosition"
  >
    <title v-if="issue && issueText">{{ issueText }}</title>
    <path class="flow-edge-hit" :d="path" data-test="designer-canvas-edge-hit" />
    <path class="flow-edge-path" :d="path" data-test="designer-canvas-edge-path" />
    <path class="flow-edge-arrow" :d="arrow" data-test="designer-canvas-edge-arrow" />
    <g
      v-if="selected && deletable"
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
      v-else-if="selected && progressed"
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
import { Position, getBezierPath, type GraphNode } from '@vue-flow/core'
import { edgeAnchors, nodeRect, type EdgeAnchors } from './edgeGeometry'

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
    progressed?: boolean
    selected?: boolean
    deletable?: boolean
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
  },
)

const emit = defineEmits<{
  /** The remove button of a selected, deletable Edge was clicked. */
  remove: []
}>()

const { t } = useI18n()

const ARROW_LENGTH = 9
const ARROW_HALF_WIDTH = 5

// Unit vector the arrow points along: into the target Node through its anchor side.
const ARROW_DIRECTION: Record<Position, [number, number]> = {
  [Position.Left]: [1, 0],
  [Position.Right]: [-1, 0],
  [Position.Top]: [0, 1],
  [Position.Bottom]: [0, -1],
}

// Where the Edge leaves and enters: the facing sides of the two cards (edgeGeometry.ts) when the
// nodes are known — they follow a dragged card at once — else the anchors handed in.
const anchors = computed<EdgeAnchors>(() =>
  props.sourceNode && props.targetNode
    ? edgeAnchors(nodeRect(props.sourceNode), nodeRect(props.targetNode))
    : {
        sourceX: props.sourceX,
        sourceY: props.sourceY,
        sourcePosition: props.sourcePosition,
        targetX: props.targetX,
        targetY: props.targetY,
        targetPosition: props.targetPosition,
      },
)

// The curve stops at the arrow's base so the line never pokes through the arrow tip.
const lineEnd = computed(() => {
  const [dx, dy] = ARROW_DIRECTION[anchors.value.targetPosition]
  return { x: anchors.value.targetX - dx * ARROW_LENGTH, y: anchors.value.targetY - dy * ARROW_LENGTH }
})

const bezier = computed(() =>
  getBezierPath({
    sourceX: anchors.value.sourceX,
    sourceY: anchors.value.sourceY,
    sourcePosition: anchors.value.sourcePosition,
    targetX: lineEnd.value.x,
    targetY: lineEnd.value.y,
    targetPosition: anchors.value.targetPosition,
  }),
)

const path = computed(() => bezier.value[0])

/** Middle of the curve (getBezierPath's label point): where the remove / locked mark sits. */
const middle = computed(() => ({ x: bezier.value[1], y: bezier.value[2] }))

const arrow = computed(() => {
  const { targetX, targetY, targetPosition } = anchors.value
  const [dx, dy] = ARROW_DIRECTION[targetPosition]
  const { x, y } = lineEnd.value
  const [px, py] = [-dy * ARROW_HALF_WIDTH, dx * ARROW_HALF_WIDTH]
  return `M${targetX},${targetY} L${x + px},${y + py} L${x - px},${y - py} Z`
})
</script>

<style scoped>
.flow-edge-path {
  fill: none;
  stroke: var(--fg2-line-strong);
  stroke-width: 1.6;
}

.flow-edge-arrow {
  fill: var(--fg2-line-strong);
  stroke: none;
}

/* flowgate.v02.0045 T0019: not yet taken — the muted line above; already taken — bright, thicker. */
.flow-edge-progressed .flow-edge-path {
  stroke: var(--fg2-status-success-fg);
  stroke-width: 2.2;
}

.flow-edge-progressed .flow-edge-arrow {
  fill: var(--fg2-status-success-fg);
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
  stroke: var(--fg2-control-hover-border);
}

.flow-edge:hover .flow-edge-arrow {
  fill: var(--fg2-control-hover-border);
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

.flow-edge.is-selected:not(.flow-edge-progressed):not(.flow-edge-issue) .flow-edge-path {
  stroke: var(--fg2-focus);
}

.flow-edge.is-selected:not(.flow-edge-progressed):not(.flow-edge-issue) .flow-edge-arrow {
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
