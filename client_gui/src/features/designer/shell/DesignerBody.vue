<template>
  <!-- Horizontal axis of the Designer: Left ↔ Center ↔ Right (0003-D §3.7.2). Left / Right
       get fixed widths from the layout state; the Center takes everything left over, down to
       its minimum. Narrower than that, only the Body scrolls sideways — columns never
       overlap. flowgate.v02.0046: a resize handle sits on each Left / Right ↔ Center line
       while that Dock is expanded; dragging it asks the Shell for a new Dock width. -->
  <div
    ref="bodyRef"
    class="designer-body"
    data-test="designer-body"
    :data-left-width="leftWidth"
    :data-right-width="rightWidth"
    :style="columns"
  >
    <slot name="left" />
    <DesignerResizeHandle
      v-if="leftResizable"
      class="designer-body-resize is-left"
      axis="x"
      :size="leftWidth"
      :grow="1"
      data-test="designer-left-resize"
      @resize="emit('resize-left', $event, bodyWidth())"
    />
    <slot name="center" />
    <DesignerResizeHandle
      v-if="rightResizable"
      class="designer-body-resize is-right"
      axis="x"
      :size="rightWidth"
      :grow="-1"
      data-test="designer-right-resize"
      @resize="emit('resize-right', $event, bodyWidth())"
    />
    <slot name="right" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import DesignerResizeHandle from './DesignerResizeHandle.vue'
import { DESIGNER_CENTER_MIN_WIDTH } from './designerLayout'

const props = withDefaults(
  defineProps<{
    /** Current left column width (expanded width or rail). */
    leftWidth: number
    /** Current right column width (expanded width or rail). */
    rightWidth: number
    /** The left Dock is expanded, so its width can be dragged. */
    leftResizable?: boolean
    /** The right Dock is expanded, so its width can be dragged. */
    rightResizable?: boolean
  }>(),
  { leftResizable: false, rightResizable: false },
)

// Each drag step carries the Body's own width, which the Shell's clamp keeps the Center's room in.
const emit = defineEmits<{
  'resize-left': [width: number, bodyWidth: number]
  'resize-right': [width: number, bodyWidth: number]
}>()

const bodyRef = ref<HTMLElement | null>(null)

function bodyWidth(): number {
  return bodyRef.value?.clientWidth ?? 0
}

const columns = computed(() => ({
  gridTemplateColumns: `${props.leftWidth}px minmax(${DESIGNER_CENTER_MIN_WIDTH}px, 1fr) ${props.rightWidth}px`,
}))
</script>

<style scoped>
.designer-body {
  position: relative;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-x: auto;
  overflow-y: hidden;
}

/* Out of the grid flow: an absolutely placed grid child takes its Dock's column as its box, so
   the handle straddles that column's inner edge and scrolls with the columns. Both lines are
   given: for an absolutely placed item an omitted end line is the container's far edge, not
   the next line, so `grid-column: 1` alone would span the whole Body and put the left handle
   on the Body's right edge. */
.designer-body > .designer-body-resize {
  position: absolute;
  grid-row: 1;
  top: 0;
  bottom: 0;
}

.designer-body > .designer-body-resize.is-left {
  grid-column: 1 / 2;
  right: calc(var(--fg2-splitter-size) / -2);
}

.designer-body > .designer-body-resize.is-right {
  grid-column: 3 / 4;
  left: calc(var(--fg2-splitter-size) / -2);
}
</style>
