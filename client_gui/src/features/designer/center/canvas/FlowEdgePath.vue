<template>
  <!-- flowgate.v02.0026 — FlowGate Edge (0003-D §6.3): a smooth curve ending in an arrow head
       at the target anchor, in the FlowGate line colour. Rendered inside Vue Flow's edge
       <svg>; it draws the arrow itself instead of using the library's marker / theme. -->
  <!-- flowgate.v02.0045: a reference Edge (document relation, no execution meaning) is drawn
       dashed so it never reads as "runs next" (D0004 §3.2). flowgate.v02.0045 S3: an editable draft
       edge has a wider hit line (double click removes it, FlowCanvas); one the last validation
       reported is drawn in the warning colour. -->
  <g
    class="flow-edge"
    :class="{ 'flow-edge-reference': edgeClass === 'reference', 'flow-edge-editable': editable, 'flow-edge-issue': issue }"
    data-test="designer-canvas-edge"
    :data-edge-id="id"
    :data-edge-class="edgeClass ?? ''"
  >
    <path v-if="editable" class="flow-edge-hit" :d="path" />
    <path class="flow-edge-path" :d="path" data-test="designer-canvas-edge-path" />
    <path class="flow-edge-arrow" :d="arrow" data-test="designer-canvas-edge-arrow" />
  </g>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Position, getBezierPath } from '@vue-flow/core'

const props = withDefaults(
  defineProps<{
    id: string
    sourceX: number
    sourceY: number
    targetX: number
    targetY: number
    sourcePosition?: Position
    targetPosition?: Position
    edgeClass?: string | null
    editable?: boolean
    issue?: boolean
  }>(),
  { sourcePosition: Position.Right, targetPosition: Position.Left, edgeClass: null, editable: false, issue: false },
)

const ARROW_LENGTH = 9
const ARROW_HALF_WIDTH = 5

// Unit vector the arrow points along: into the target Node through its anchor side.
const ARROW_DIRECTION: Record<Position, [number, number]> = {
  [Position.Left]: [1, 0],
  [Position.Right]: [-1, 0],
  [Position.Top]: [0, 1],
  [Position.Bottom]: [0, -1],
}

// The curve stops at the arrow's base so the line never pokes through the arrow tip.
const lineEnd = computed(() => {
  const [dx, dy] = ARROW_DIRECTION[props.targetPosition]
  return { x: props.targetX - dx * ARROW_LENGTH, y: props.targetY - dy * ARROW_LENGTH }
})

const path = computed(
  () =>
    getBezierPath({
      sourceX: props.sourceX,
      sourceY: props.sourceY,
      sourcePosition: props.sourcePosition,
      targetX: lineEnd.value.x,
      targetY: lineEnd.value.y,
      targetPosition: props.targetPosition,
    })[0],
)

const arrow = computed(() => {
  const [dx, dy] = ARROW_DIRECTION[props.targetPosition]
  const { x, y } = lineEnd.value
  const [px, py] = [-dy * ARROW_HALF_WIDTH, dx * ARROW_HALF_WIDTH]
  return `M${props.targetX},${props.targetY} L${x + px},${y + py} L${x - px},${y - py} Z`
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

.flow-edge-reference .flow-edge-path {
  stroke-dasharray: 5 4;
  stroke-width: 1.2;
}

.flow-edge-hit {
  fill: none;
  stroke: transparent;
  stroke-width: 12;
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
</style>
