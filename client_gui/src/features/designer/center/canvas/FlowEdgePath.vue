<template>
  <!-- flowgate.v02.0026 — FlowGate Edge (0003-D §6.3): a smooth curve ending in an arrow head
       at the target anchor, in the FlowGate line colour. Rendered inside Vue Flow's edge
       <svg>; it draws the arrow itself instead of using the library's marker / theme. -->
  <g class="flow-edge" data-test="designer-canvas-edge" :data-edge-id="id">
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
  }>(),
  { sourcePosition: Position.Right, targetPosition: Position.Left },
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

.flow-edge:hover .flow-edge-path {
  stroke: var(--fg2-control-hover-border);
}

.flow-edge:hover .flow-edge-arrow {
  fill: var(--fg2-control-hover-border);
}
</style>
